#!/usr/bin/env bash

# ----------------------------------------------------------------------------
# Verification script — checks the status of the SSH-safe CIS hardening
# changes applied earlier. READ-ONLY: this script does not change anything,
# it only reports status. Safe to run any time, as many times as you like.
# ----------------------------------------------------------------------------

GREEN="\e[1;32m"
YELLOW="\e[1;33m"
RED="\e[1;31m"
BLUE="\e[1;34m"
RESET="\e[0m"

ok()   { echo -e "${GREEN}[OK]${RESET} $1"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
bad()  { echo -e "${RED}[FAIL]${RESET} $1"; }
info() { echo -e "${BLUE}[INFO]${RESET} $1"; }

section() { echo -e "\n${BLUE}==== $1 ====${RESET}"; }

# --- 0. Basic system info ---
section "System Info"
echo "Hostname: $(hostname)"
echo "Kernel:   $(uname -r)"
echo "Uptime:   $(uptime -p)"
echo "Date:     $(date)"

# --- 1. Kernel modules disabled ---
section "Disabled Kernel Modules (Sections 1 & 6)"
for mod in cramfs freevxfs hfs hfsplus jffs2 dccp tipc rds sctp; do
    if lsmod | grep -q "^${mod}"; then
        bad "$mod is still LOADED"
    elif [ -f "/etc/modprobe.d/${mod}.conf" ]; then
        ok "$mod is disabled (blacklisted, not loaded)"
    else
        warn "$mod has no blacklist conf file and is not loaded (may be fine if never built)"
    fi
done

# --- 2. /tmp mount ---
section "/tmp Mount (Section 2)"
if mount | grep -q "on /tmp type tmpfs"; then
    ok "/tmp is mounted as tmpfs"
    mount | grep "on /tmp "
else
    bad "/tmp is NOT mounted as tmpfs"
fi

# --- 3. AppArmor ---
section "AppArmor (Section 3)"
if systemctl is-active --quiet apparmor; then
    ok "apparmor service is active"
else
    bad "apparmor service is NOT active"
fi
if command -v aa-status &>/dev/null; then
    ENFORCE_COUNT=$(aa-status 2>/dev/null | grep -c "profiles are in enforce mode" || true)
    aa-status 2>/dev/null | grep "profiles are loaded\|profiles are in enforce mode\|profiles are in complain mode"
fi
if grep -q "apparmor=1" /etc/default/grub; then
    ok "GRUB has apparmor=1 configured"
else
    warn "GRUB does not show apparmor=1 (check /etc/default/grub manually)"
fi

# --- 4. Kernel / sysctl parameters ---
section "Kernel Sysctl Parameters (Section 5)"
declare -A EXPECTED=(
    ["kernel.randomize_va_space"]="2"
    ["kernel.yama.ptrace_scope"]="1"
    ["fs.suid_dumpable"]="0"
    ["net.ipv4.ip_forward"]="0"
    ["net.ipv4.conf.all.accept_redirects"]="0"
    ["net.ipv4.conf.all.accept_source_route"]="0"
    ["net.ipv4.tcp_syncookies"]="1"
    ["net.ipv4.conf.all.log_martians"]="1"
    ["net.ipv6.conf.all.disable_ipv6"]="1"
)
for key in "${!EXPECTED[@]}"; do
    current=$(sysctl -n "$key" 2>/dev/null || echo "MISSING")
    expected="${EXPECTED[$key]}"
    if [ "$current" == "$expected" ]; then
        ok "$key = $current"
    else
        bad "$key = $current (expected $expected)"
    fi
done

# --- 5. Services stopped/disabled ---
section "Unnecessary Services (Section 7)"
SERVICES=(autofs avahi-daemon isc-dhcp-server bind9 dnsmasq vsftpd slapd \
    dovecot-imapd dovecot-pop3d nfs-kernel-server ypserv cups rpcbind \
    rsync samba snmpd tftpd-hpa squid apache2 nginx xinetd bluetooth)
for svc in "${SERVICES[@]}"; do
    if dpkg-query -s "$svc" &>/dev/null; then
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            bad "$svc is installed AND still running"
        else
            ok "$svc is installed but stopped/inactive"
        fi
    fi
done
info "(services not printed above are simply not installed — that's fine)"

# --- 6. Cron/At permissions ---
section "Cron & At (Section 9)"
for f in /etc/cron.allow /etc/at.allow; do
    if [ -f "$f" ]; then
        perms=$(stat -c "%a %U:%G" "$f")
        ok "$f exists ($perms)"
    else
        warn "$f does not exist"
    fi
done

# --- 7. PAM / faillock / pwquality ---
section "PAM Password Policy (Section 10)"
if [ -f /etc/security/pwquality.conf.d/50-pwcomplexity.conf ]; then
    ok "pwquality complexity file present:"
    cat /etc/security/pwquality.conf.d/50-pwcomplexity.conf | sed 's/^/     /'
else
    bad "pwquality complexity file missing"
fi
if [ -f /etc/security/faillock.conf ]; then
    ok "faillock.conf present:"
    grep -E "^deny|^unlock_time|^even_deny_root|^root_unlock_time" /etc/security/faillock.conf | sed 's/^/     /'
else
    bad "faillock.conf missing"
fi
if grep -q "pam_faillock.so" /etc/pam.d/common-auth 2>/dev/null; then
    ok "pam_faillock is wired into common-auth"
else
    warn "pam_faillock not found in common-auth (check pam-auth-update status)"
fi

# --- 8. SSH configuration — the most important section ---
section "SSH Configuration (Section 11) — CRITICAL"
SSH_UNIT=""
if systemctl list-unit-files | grep -q '^ssh\.service'; then
    SSH_UNIT="ssh"
elif systemctl list-unit-files | grep -q '^sshd\.service'; then
    SSH_UNIT="sshd"
fi

if [ -n "$SSH_UNIT" ]; then
    if systemctl is-active --quiet "$SSH_UNIT"; then
        ok "$SSH_UNIT.service is active and running"
    else
        bad "$SSH_UNIT.service is NOT active — SSH may be down!"
    fi
else
    bad "Could not find ssh.service or sshd.service unit at all"
fi

if sshd -t 2>/dev/null; then
    ok "sshd config syntax is valid (sshd -t passed)"
else
    bad "sshd config syntax test FAILED — review /etc/ssh/sshd_config"
fi

echo "Key sshd_config directives currently active:"
for directive in PermitRootLogin PasswordAuthentication PermitEmptyPasswords \
    MaxAuthTries ClientAliveInterval LogLevel Port; do
    value=$(sshd -T 2>/dev/null | grep -i "^${directive} " || echo "${directive} <not found>")
    echo "     $value"
done

CURRENT_PASS_AUTH=$(sshd -T 2>/dev/null | grep -i "^passwordauthentication " | awk '{print $2}')
if [ "$CURRENT_PASS_AUTH" == "yes" ]; then
    ok "PasswordAuthentication is 'yes' — you can still log in with your password"
else
    bad "PasswordAuthentication is '$CURRENT_PASS_AUTH' — this could lock you out if you rely on password login!"
fi

# --- 9. UFW Firewall ---
section "UFW Firewall (Section 12) — CRITICAL"
if command -v ufw &>/dev/null; then
    if systemctl is-active --quiet ufw; then
        ok "ufw service is active"
    else
        bad "ufw service is NOT active"
    fi
    echo "Current UFW status:"
    ufw status verbose | sed 's/^/     /'
    if ufw status | grep -qE "22/tcp\s+ALLOW"; then
        ok "Port 22/tcp is explicitly ALLOWED"
    else
        bad "Port 22/tcp does NOT show as ALLOWED — check immediately: sudo ufw allow 22/tcp"
    fi
else
    warn "ufw is not installed"
fi

# --- 10. Auditd ---
section "Auditd (Section 13)"
if systemctl is-active --quiet auditd; then
    ok "auditd is active"
else
    bad "auditd is NOT active"
fi
if [ -f /etc/audit/auditd.conf ]; then
    echo "Disk action settings (should NOT be 'halt'):"
    grep -E "^disk_full_action|^disk_error_action|^space_left_action|^admin_space_left_action" /etc/audit/auditd.conf | sed 's/^/     /'
    if grep -qi "halt" /etc/audit/auditd.conf; then
        bad "auditd.conf still contains 'halt' somewhere — this could shut the server down on disk-full!"
    else
        ok "No 'halt' action found in auditd.conf"
    fi
fi
if [ -f /etc/audit/rules.d/99-finalize.rules ] && grep -q "^-e 2" /etc/audit/rules.d/99-finalize.rules 2>/dev/null; then
    warn "Immutable audit rules (-e 2) are set — you'll need a reboot to change audit rules"
else
    ok "Audit rules are not locked immutable (can be changed without reboot)"
fi

# --- 11. AIDE ---
section "AIDE (Section 14)"
if command -v aide &>/dev/null; then
    ok "aide is installed"
    if [ -f /var/lib/aide/aide.db ]; then
        ok "AIDE database exists"
    else
        warn "AIDE database not found at /var/lib/aide/aide.db"
    fi
    if systemctl is-enabled --quiet dailyaidecheck.timer 2>/dev/null; then
        ok "dailyaidecheck.timer is enabled"
    else
        warn "dailyaidecheck.timer is not enabled"
    fi
else
    warn "aide is not installed"
fi

# --- 12. File permissions ---
section "Critical File Permissions (Section 15)"
declare -A FILE_PERMS=(
    ["/etc/passwd"]="644"
    ["/etc/shadow"]="640"
    ["/etc/group"]="644"
    ["/etc/gshadow"]="640"
)
for f in "${!FILE_PERMS[@]}"; do
    if [ -f "$f" ]; then
        actual=$(stat -c "%a" "$f")
        expected="${FILE_PERMS[$f]}"
        if [ "$actual" == "$expected" ]; then
            ok "$f permissions = $actual"
        else
            warn "$f permissions = $actual (expected $expected)"
        fi
    fi
done

# --- 13. Login banners ---
section "Login Banners (Section 16)"
for f in /etc/issue /etc/issue.net; do
    if [ -s "$f" ]; then
        ok "$f is set: $(cat "$f")"
    else
        warn "$f is empty or missing"
    fi
done

# --- 14. Apport / prelink ---
section "Apport & Prelink (Sections 17-18)"
if dpkg-query -s apport &>/dev/null; then
    warn "apport is still installed"
else
    ok "apport is not installed"
fi
if dpkg-query -s prelink &>/dev/null; then
    warn "prelink is still installed"
else
    ok "prelink is not installed"
fi

# --- 15. Pending kernel/reboot check ---
section "Kernel / Reboot Status"
RUNNING_KERNEL=$(uname -r)
LATEST_KERNEL=$(dpkg -l 'linux-image-*' 2>/dev/null | awk '/^ii/{print $2}' | grep -E '^linux-image-[0-9]' | sed 's/linux-image-//' | sort -V | tail -1)
if [ -n "$LATEST_KERNEL" ]; then
    if [ "$RUNNING_KERNEL" == "$LATEST_KERNEL" ]; then
        ok "Running the latest installed kernel ($RUNNING_KERNEL)"
    else
        warn "Running kernel ($RUNNING_KERNEL) differs from latest installed ($LATEST_KERNEL) — reboot again if this persists"
    fi
fi
if [ -f /var/run/reboot-required ]; then
    warn "System still reports a reboot is required: $(cat /var/run/reboot-required.pkgs 2>/dev/null)"
else
    ok "No pending reboot required"
fi

# --- Summary ---
section "SUMMARY"
echo "Review any [FAIL] or [WARN] lines above."
echo "Most critical checks for YOUR situation (no console access):"
echo "  1. SSH service active + PasswordAuthentication yes"
echo "  2. UFW port 22/tcp ALLOW rule present"
echo "  3. No 'halt' in auditd.conf"
echo ""
echo "If you see any [FAIL] on those three, do NOT close this terminal —"
echo "open a second one and confirm 'ssh bassem@<server-ip>' still works"
echo "before troubleshooting further."
