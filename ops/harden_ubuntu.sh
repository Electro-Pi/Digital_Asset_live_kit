#!/usr/bin/env bash

# ----------------------------------------------------------------------------
# Bash Script for Ubuntu 24.04 (CIS Benchmark Hardening - SSH-SAFE EDITION)
#
# WARNING: This script is for educational and testing purposes. Review
#          thoroughly before use. Some changes require a reboot to take
#          full effect.
#----------------------------------------------------------------------------
# READ THIS BEFORE RUNNING:
#   1. Confirm your SSH port really is 22 (you confirmed this: yes, 22).
#   2. Keep your CURRENT ssh session open while you test a NEW ssh
#      connection in a second terminal after the script finishes. Do NOT
#      close your current session until the new login is confirmed to work.
#   3. This script still makes many real changes to your system. Read every
#      section before running.
# ----------------------------------------------------------------------------

set -e  # Exit immediately if a command exits with a non-zero status.
# set -x # Uncomment for debugging (prints each command)

# --- Helper Functions ---
print_color() {
    echo -e "\e[1;${1}m${2}\e[0m"
}
print_success() { print_color "32" "[SUCCESS] $1"; }
print_warning() { print_color "33" "[WARNING] $1"; }
print_info()    { print_color "34" "[INFO] $1"; }
print_error()   { print_color "31" "[ERROR] $1"; }

run_command() {
    print_info "Running: $*"
    "$@" || {
        print_error "Command failed: $*"
        exit 1
    }
    print_success "Command succeeded: $*"
}

backup_file() {
    local file=$1
    if [ -f "$file" ]; then
        local backup="${file}.backup.$(date +%Y%m%d%H%M%S)"
        print_warning "Creating backup of $file to $backup"
        cp -f "$file" "$backup"
    fi
}

# --- Pre-flight Checks ---
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script as root (use sudo)."
    exit 1
fi

print_info "Starting SSH-safe CIS Hardening Script for Ubuntu 24.04"
print_warning "This script makes significant changes to the system. Proceed with caution."
print_warning "KEEP your current SSH session OPEN and test a NEW connection in a second window after this finishes."
sleep 5

# --- 1. Initial Setup: Disable Unused Filesystems ---
print_info "--- Section 1: Disabling Unused Filesystems ---"
disable_module() {
    local mod_name=$1
    local conf_file="/etc/modprobe.d/${mod_name}.conf"
    print_info "Disabling module: $mod_name"
    sed -i "/$mod_name/d" "$conf_file" 2>/dev/null || true
    echo "install $mod_name /bin/false" >> "$conf_file"
    echo "blacklist $mod_name" >> "$conf_file"
    modprobe -r "$mod_name" 2>/dev/null || true
    rmmod "$mod_name" 2>/dev/null || true
    print_success "Module $mod_name disabled"
}

disable_module "cramfs"
disable_module "freevxfs"
disable_module "hfs"
disable_module "hfsplus"
disable_module "jffs2"

# --- 2. Filesystem Mount Options (/tmp only) ---
# NOTE: The original script's /home -> separate partition section (which ran
# fdisk + mkfs against /dev/vdb) has been REMOVED ENTIRELY. It had nothing
# to do with SSH safety, but on a hosting VPS it risks destroying an
# existing disk with no confirmation prompt. Skip it; it's not needed for
# "level 2" hardening.
print_info "--- Section 2: Configuring Mount Options (/tmp) ---"
print_info "Configuring /tmp as tmpfs"
backup_file /etc/fstab
if ! grep -q "/tmp" /etc/fstab; then
    echo "tmpfs /tmp tmpfs defaults,rw,nosuid,nodev,noexec,relatime,size=2G 0 0" >> /etc/fstab
    systemctl daemon-reload
    if mount | grep -q "on /tmp "; then
        mount -o remount /tmp
    else
        mount /tmp
    fi
    print_success "/tmp configured as tmpfs"
else
    print_warning "/tmp entry already exists in /etc/fstab. Please review and modify manually."
fi

# --- 3. AppArmor ---
print_info "--- Section 3: Configuring AppArmor ---"
run_command apt install -y apparmor apparmor-utils
print_info "Updating GRUB to include AppArmor parameters"
backup_file /etc/default/grub
if ! grep -q "apparmor=1" /etc/default/grub; then
    sed -i 's/GRUB_CMDLINE_LINUX=""/GRUB_CMDLINE_LINUX="apparmor=1 security=apparmor"/g' /etc/default/grub
    run_command update-grub
else
    print_warning "AppArmor parameters may already be set in GRUB. Please check."
fi
run_command aa-enforce /etc/apparmor.d/*

# --- 4. Bootloader Password ---
# Left as a MANUAL step on purpose. Automating GRUB password entry is not
# related to SSH login and is risky to script unattended — skip automating it.
print_info "--- Section 4: GRUB Password (manual step, not automated) ---"
print_warning "Please run the following commands manually if you want a GRUB password:"
cat << EOF
    grub-mkpasswd-pbkdf2
    # Edit /etc/grub.d/40_custom and add:
        set superusers="<username>"
        password_pbkdf2 <username> <encrypted-password>
    update-grub
EOF

# --- 5. Kernel Parameters (Sysctl) ---
print_info "--- Section 5: Setting Secure Kernel Parameters ---"
SYSCTL_CONF_DIR="/etc/sysctl.d"
mkdir -p "$SYSCTL_CONF_DIR"

echo "kernel.randomize_va_space = 2" >> "$SYSCTL_CONF_DIR/60-kernel_sysctl.conf"
sysctl -w kernel.randomize_va_space=2

echo "kernel.yama.ptrace_scope = 1" >> "$SYSCTL_CONF_DIR/60-kernel_sysctl.conf"
sysctl -w kernel.yama.ptrace_scope=1

echo "fs.suid_dumpable = 0" >> "$SYSCTL_CONF_DIR/60-fs_sysctl.conf"
sysctl -w fs.suid_dumpable=0

NET_CONF="$SYSCTL_CONF_DIR/60-netipv4_sysctl.conf"
cat << EOF > "$NET_CONF"
net.ipv4.ip_forward = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.tcp_syncookies = 1
EOF

while read -r param; do
    sysctl -w "$param"
done < <(grep -v '^#' "$NET_CONF")
sysctl -w net.ipv4.route.flush=1

# NOTE: kept IPv6 disabling from the original — this does not affect an
# IPv4 SSH session (your listening sockets are on 0.0.0.0:22 AND [::]:22,
# but you are connecting over IPv4, per your `ss` output). If you ever
# connect over IPv6, remove this block first.
print_info "Disabling IPv6"
backup_file /etc/sysctl.conf
cat << EOF >> /etc/sysctl.conf
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
EOF
sysctl -p

# --- 6. Uncommon Network Protocols (Disable Modules) ---
print_info "--- Section 6: Disabling Uncommon Network Protocols ---"
disable_module "dccp"
disable_module "tipc"
disable_module "rds"
disable_module "sctp"

# --- 7. Services and Clients ---
# NOTE: "sshd"/"ssh" is intentionally NOT in this list, and never was.
print_info "--- Section 7: Removing or Stopping Unnecessary Services ---"
SERVICES=(
    "autofs" "avahi-daemon" "isc-dhcp-server" "bind9" "dnsmasq" "vsftpd"
    "slapd" "dovecot-imapd" "dovecot-pop3d" "nfs-kernel-server" "ypserv"
    "cups" "rpcbind" "rsync" "samba" "snmpd" "tftpd-hpa" "squid"
    "apache2" "nginx" "xinetd" "bluetooth"
)
CLIENTS=(
    "nis" "rsh-client" "talk" "telnet" "ldap-utils" "ftp"
)

for service in "${SERVICES[@]}"; do
    if dpkg-query -s "$service" &>/dev/null; then
        print_warning "$service is installed. Stopping and disabling..."
        systemctl stop "$service" 2>/dev/null || true
        systemctl disable "$service" 2>/dev/null || true
    else
        print_info "$service is not installed."
    fi
done

for client in "${CLIENTS[@]}"; do
    if dpkg-query -s "$client" &>/dev/null; then
        print_warning "$client is installed. Consider purging it."
    fi
done

# --- 8. Time Synchronization ---
print_info "--- Section 8: Configuring systemd-timesyncd ---"
TIMESYNC_CONF="/etc/systemd/timesyncd.conf.d/60-timesyncd.conf"
mkdir -p /etc/systemd/timesyncd.conf.d/
cat << EOF > "$TIMESYNC_CONF"
[Time]
NTP=time.nist.gov
FallbackNTP=time-a-g.nist.gov time-bg.nist.gov time-c-g.nist.gov
EOF
systemctl enable --now systemd-timesyncd.service

# --- 9. Cron and At ---
print_info "--- Section 9: Securing Cron and At ---"
run_command systemctl enable --now cron

for dir in /etc/crontab /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.d; do
    chown root:root "$dir"
    chmod og-rwx "$dir"
done

touch /etc/cron.allow
chown root:root /etc/cron.allow
chmod u-x,g-wx,o-rwx /etc/cron.allow

touch /etc/at.allow
chown root:root /etc/at.allow
chmod u-x,g-wx,o-rwx /etc/at.allow
[ -e "/etc/at.deny" ] && chown root:root /etc/at.deny && chmod u-x,g-wx,o-rwx /etc/at.deny

# --- 10. PAM and Password Policies ---
# IMPORTANT: You log in with a PASSWORD, not an SSH key. faillock below
# will lock out a login after repeated *wrong-password* attempts for
# `deny` tries. If YOU mistype your own password `deny` times in a row,
# you will lock yourself out for `unlock_time` seconds. deny=5 / unlock=600
# below gives you a bit more room than the original (deny=4) before that
# happens. This does not affect a session that is already logged in.
print_info "--- Section 10: Configuring PAM and Password Policies ---"
apt install -y libpam-pwquality libpam-modules

backup_file /etc/pam.d/common-password
backup_file /etc/pam.d/common-auth
backup_file /etc/security/faillock.conf
backup_file /etc/security/pwquality.conf

mkdir -p /etc/security/pwquality.conf.d
cat << EOF > /etc/security/pwquality.conf.d/50-pwcomplexity.conf
minlen = 14
minclass = 3
maxrepeat = 3
maxsequence = 3
EOF

cat << EOF > /etc/security/faillock.conf
deny = 5
unlock_time = 600
even_deny_root
root_unlock_time = 60
EOF
print_warning "faillock: you get 5 wrong PASSWORD attempts before a 10-minute lockout on your own account. Type carefully."

cat << 'EOF' > /usr/share/pam-configs/faillock
Name: Enable pam_faillock to deny access
Default: yes
Priority: 0
Auth-Type: Primary
Auth: [default=die] pam_faillock.so authfail
EOF

cat << 'EOF' > /usr/share/pam-configs/faillock_notify
Name: Notify of failed login attempts and reset count upon success
Default: yes
Priority: 1024
Auth-Type: Primary
Auth: requisite pam_faillock.so preauth
Account-Type: Primary
Account: required pam_faillock.so
EOF

pam-auth-update --enable faillock --enable faillock_notify --force

# --- 11. SSH Hardening ---
# This is the section most likely to affect your login. Changes here are
# deliberately conservative:
#   - PermitRootLogin no        -> SAFE: you log in as "bassem", not root.
#   - PasswordAuthentication yes -> ADDED EXPLICITLY: you log in with a
#     password, so this MUST stay enabled. Original script never set this
#     key at all, relying on the (usually "yes") distro default — we now
#     pin it so a future default change can't silently disable it.
#   - Port is left untouched (still 22, as you confirmed).
print_info "--- Section 11: Hardening SSH Configuration ---"
SSHD_CONFIG="/etc/ssh/sshd_config"
backup_file "$SSHD_CONFIG"

chmod u-x,og-rwx "$SSHD_CONFIG"
chown root:root "$SSHD_CONFIG"

find /etc/ssh -type f -name 'ssh_host_*_key' -exec chmod u-x,og-rwx {} \; -exec chown root:root {} \;
find /etc/ssh -type f -name '*.pub' -exec chmod u-x,go-wx {} \; -exec chown root:root {} \;

cat << 'EOF' >> "$SSHD_CONFIG"

# CIS Hardening Settings (SSH-safe edition)
Ciphers -3des-cbc,aes128-cbc,aes192-cbc,aes256-cbc,chacha20-poly1305@openssh.com
ClientAliveInterval 15
ClientAliveCountMax 3
DisableForwarding yes
GSSAPIAuthentication no
HostbasedAuthentication no
IgnoreRhosts yes
LoginGraceTime 60
LogLevel INFO
MACs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com,hmac-sha2-256,hmac-sha2-512
MaxAuthTries 4
MaxSessions 10
MaxStartups 10:30:60
PermitEmptyPasswords no
PermitRootLogin no
PermitUserEnvironment no
PasswordAuthentication yes
UsePAM yes
Banner /etc/issue.net
EOF

echo "Authorized users only. All activity may be monitored and reported." > /etc/issue.net

# Validate config BEFORE restarting sshd — if this fails, we bail out
# without touching the running sshd process, so your current session and
# the service stay exactly as they were.
if sshd -t; then
    # Ubuntu 24.04 renamed the systemd unit to ssh.service (sshd.service
    # doesn't exist as a unit name there). Try ssh.service first, fall
    # back to sshd.service for other distros/older Ubuntu.
    if systemctl list-unit-files | grep -q '^ssh\.service'; then
        SSH_UNIT="ssh"
    else
        SSH_UNIT="sshd"
    fi
    systemctl restart "$SSH_UNIT"
    print_success "SSH configuration validated and applied ($SSH_UNIT.service restarted)."
else
    print_error "sshd config test FAILED. NOT restarting SSH. Your existing SSH session and running daemon are untouched. Check $SSHD_CONFIG (and its .backup file) before proceeding."
    exit 1
fi

# --- 12. UFW Firewall ---
# REORDERED vs the original script: SSH is allowed and the rule is in
# place BEFORE we reset/enable UFW, and default policies are set to a
# state that can never leave you without an explicit "allow 22" rule
# already active. We also do NOT set `ufw default deny outgoing`, since a
# stray/failed outgoing rule reorder was one of the ways this could go
# wrong; outgoing stays allowed (matching the original script's end state
# of "allow out on all" anyway, but without the risky reset-then-fix path).
print_info "--- Section 12: Configuring UFW Firewall (SSH-safe order) ---"
apt install -y ufw

print_info "Setting default policies (outgoing allowed, incoming denied, routed denied)"
ufw default allow outgoing
ufw default deny incoming
ufw default deny routed

print_info "Allowing loopback traffic"
ufw allow in on lo
ufw allow out on lo

print_info "Explicitly allowing SSH on port 22/tcp BEFORE enabling the firewall"
ufw allow 22/tcp

print_info "Enabling UFW"
ufw --force enable
systemctl enable ufw.service

if ufw status | grep -q "22/tcp.*ALLOW"; then
    print_success "Confirmed: port 22/tcp is allowed through UFW."
else
    print_error "Could NOT confirm 22/tcp allow rule is active. Run 'sudo ufw status' NOW in a way that keeps this session open, and add it manually if missing: sudo ufw allow 22/tcp"
fi
print_warning "Before closing this session: open a SECOND terminal and confirm 'ssh bassem@<server-ip>' still works."

# --- 13. Auditd Configuration ---
# CHANGED: disk_full_action was "halt" in the original, which shuts the
# entire server down if the audit log partition fills up. Changed to
# SYSLOG so a full disk only logs a warning and never stops the machine —
# important since you have no console fallback.
print_info "--- Section 13: Configuring Auditd ---"
apt install -y auditd audispd-plugins

AUDITD_CONF="/etc/audit/auditd.conf"
backup_file "$AUDITD_CONF"
sed -i 's/^max_log_file_action =.*/max_log_file_action = keep_logs/' "$AUDITD_CONF"
sed -i 's/^disk_full_action =.*/disk_full_action = SYSLOG/' "$AUDITD_CONF"
sed -i 's/^disk_error_action =.*/disk_error_action = SYSLOG/' "$AUDITD_CONF"
sed -i 's/^space_left_action =.*/space_left_action = SYSLOG/' "$AUDITD_CONF"
sed -i 's/^admin_space_left_action =.*/admin_space_left_action = SYSLOG/' "$AUDITD_CONF"
sed -i 's/^log_group =.*/log_group = adm/' "$AUDITD_CONF"

RULES_DIR="/etc/audit/rules.d"
cat << 'EOF' > "$RULES_DIR/50-scope.rules"
-w /etc/sudoers -p wa -k scope
-w /etc/sudoers.d -p wa -k scope
EOF

cat << 'EOF' > "$RULES_DIR/50-user_emulation.rules"
-a always,exit -F arch=b64 -C euid!=uid -F auid!=unset -S execve -k user_emulation
-a always,exit -F arch=b32 -C euid!=uid -F auid!=unset -S execve -k user_emulation
EOF

# NOTE: the "-e 2" immutable-rules finalizer from the original script has
# been REMOVED. That setting locks audit rules until reboot — harmless to
# SSH itself, but it blocks you from adjusting audit rules later without a
# reboot, which is an unnecessary restriction here.

augenrules --load
systemctl enable --now auditd
print_success "Auditd configured (non-halting disk actions)."

# --- 14. AIDE ---
print_info "--- Section 14: Installing and Configuring AIDE ---"
apt install -y aide aide-common
aideinit
mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db
systemctl enable --now dailyaidecheck.timer

# --- 15. Final File Permissions ---
print_info "--- Section 15: Setting Secure File Permissions ---"
chmod 644 /etc/passwd
chown root:root /etc/passwd
chmod 640 /etc/shadow
chown root:shadow /etc/shadow
chmod 644 /etc/group
chown root:root /etc/group
chmod 640 /etc/gshadow
chown root:shadow /etc/gshadow
chmod 644 /etc/passwd-
chown root:root /etc/passwd-
chmod 640 /etc/shadow-
chown root:shadow /etc/shadow-
chmod 644 /etc/group-
chown root:root /etc/group-
chmod 640 /etc/gshadow-
chown root:shadow /etc/gshadow-
chmod 644 /etc/shells
chown root:root /etc/shells
[ -e "/etc/security/opasswd" ] && chmod 600 /etc/security/opasswd && chown root:root /etc/security/opasswd
[ -e "/etc/security/opasswd.old" ] && chmod 600 /etc/security/opasswd.old && chown root:root /etc/security/opasswd.old

# --- 16. Login Banners ---
print_info "--- Section 16: Setting Login Banners ---"
BANNER_MSG="Authorized users only. All activity may be monitored and reported."
echo "$BANNER_MSG" > /etc/issue
echo "$BANNER_MSG" > /etc/issue.net

# --- 17. Apport (Crash Reporting) ---
print_info "--- Section 17: Disabling Apport ---"
if [ -f /etc/default/apport ]; then
    sed -i 's/^enabled=.*/enabled=0/' /etc/default/apport
fi
systemctl stop apport.service 2>/dev/null || true
systemctl mask apport.service 2>/dev/null || true
apt purge -y apport 2>/dev/null || true

# --- 18. Prelink ---
print_info "--- Section 18: Removing Prelink ---"
if dpkg-query -s prelink &>/dev/null; then
    apt purge -y prelink
fi

# --- 19. Remove Unused Packages ---
# NOTE: "iptables-persistent" removed from the purge list — if it happens
# to be present and managing separate iptables rules alongside UFW, purging
# it mid-hardening is an unnecessary risk to network rule persistence.
# Review manually if you want it gone.
print_info "--- Section 19: Removing Unnecessary Packages ---"
apt purge -y xserver-common telnet rsh-client talk 2>/dev/null || true

# --- Completion ---
print_success "CIS hardening (SSH-safe edition) completed."
print_warning "DO NOT close this terminal yet."
print_warning "Open a SECOND terminal now and confirm: ssh bassem@<server-ip>"
print_warning "Only close this session after the new login is confirmed working."
print_warning "A reboot is recommended once you've confirmed SSH access, to apply AppArmor/GRUB changes."

exit 0
