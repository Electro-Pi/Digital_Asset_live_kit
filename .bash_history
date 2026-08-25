sudo whoami
exit
ls
ll
sudo ./script.sh 
sudo ./check.sh 
sudo reboot now
pwd
ls
exit
sudo apt install nginx
systemctl status nginx.service 
ls -a
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
exit
rm -rf ~/.vscode-server
exit
ip a
sudo apt update && sudo apt install -y tar curl
rm -rf ~/.vscode-server ~/.cursor-server
cat ~/.cursor-server/var/log/*/remoteagent.log 2>/dev/null || echo "No logs yet"
rm -rf ~/.cursor-server ~/.vscode-server
ldd --version
tail -n 20 ~/.bashrc
mkdir -p ~/.cursor-server
code --install-extension ms-vscode-remote.remote-ssh
cursor --install-extension ms-vscode-remote.remote-ssh
mkdir -p ~/.cursor-server/bin
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
SERVER_DIR="$HOME/.cursor-server/bin/$COMMIT"
mkdir -p "$SERVER_DIR"
cd /tmp
curl -L --retry 3 "https://www.codecursor.com/api/releases/server?commit=$COMMIT&platform=linux-x64" -o cursor-server.tar.gz || curl -L --retry 3 "https://update.codecursor.com/commit:ae3a2b7231dd56194447fe4570dfdc61640b1e90/server-linux-x64/stable" -o cursor-server.tar.gz
# If the direct download fails via standard links, let's use Cursor's fallback CDN naming convention:
tar -xzf cursor-server.tar.gz -C "$SERVER_DIR" --strip-components=1 2>/dev/null || echo "Check archive"
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
SERVER_DIR="$HOME/.cursor-server/bin/$COMMIT"
mkdir -p "$SERVER_DIR"
cd /tmp
# Download using Microsoft's official VS Code commit release pipeline
curl -L --retry 3 "https://update.code.visualstudio.com/commit:$COMMIT/server-linux-x64/stable" -o vscode-server.tar.gz
# Unpack it into your cursor-server directory
tar -xzf vscode-server.tar.gz -C "$SERVER_DIR" --strip-components=1
rm -rf ~/.cursor-server ~/.vscode-server
sudo apt update && sudo apt install -y curl wget tar libstdc++6
exit
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
mkdir -p ~/.cursor-server/bin/$COMMIT
cd ~/.cursor-server/bin/$COMMIT
# Download the stable Linux x64 core server bundle
curl -sSL "https://update.code.visualstudio.com/commit:$COMMIT/server-linux-x64/stable" -o server.tar.gz
# Unpack it 
tar -xzf server.tar.gz --strip-components=1
rm server.tar.gz
exit
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
mkdir -p ~/.cursor-server/bin/$COMMIT
cd ~/.cursor-server/bin/$COMMIT
# Download using Cursor's explicit update server route
curl -sSL "https://www.cursor.com/api/releases/download?commit=$COMMIT&platform=linux-x64" -o server.tar.gz
# If that file is valid, extract it:
tar -xzf server.tar.gz --strip-components=1 2>/dev/null && rm server.tar.gz || echo "Extraction failed, let's check download"
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
mkdir -p ~/.cursor-server/bin/$COMMIT
cd ~/.cursor-server/bin/$COMMIT
# Pull the official fallback release tarball via alternative routing
wget -O server.tar.gz "https://update.code.visualstudio.com/commit:$COMMIT/server-linux-x64/stable" || curl -L "https://update.code.visualstudio.com/commit:$COMMIT/server-linux-x64/stable" -o server.tar.gz
# If it downloaded, let's inspect what it is or unpack it:
tar -xzf server.tar.gz --strip-components=1 2>/dev/null || echo "Downloaded file needs check"
cd ~
rm -rf ~/.cursor-server/bin/*
mkdir -p ~/.vscode-server/bin/$COMMIT
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
mkdir -p ~/.cursor-server/bin/$COMMIT
cd ~/.cursor-server/bin/$COMMIT
# Grab the latest general stable Linux x64 release directly from Microsoft's redirect-safe endpoint
wget -O server.tar.gz "https://update.code.visualstudio.com/latest/server-linux-x64/stable"
tar -xzf server.tar.gz --strip-components=1
rm server.tar.gz
cd ~/.cursor-server/bin/ae3a2b7231dd56194447fe4570dfdc61640b1e90
tar -xzf server.tar.gz --strip-components=1
rm server.tar.gz
exit
COMMIT="ae3a2b7231dd56194447fe4570dfdc61640b1e90"
mkdir -p ~/.cursor-server/bin/$COMMIT
cd ~/.cursor-server/bin/$COMMIT
# Download the archive fresh
curl -L "https://update.code.visualstudio.com/latest/server-linux-x64/stable" -o server.tar.gz
# Unpack it properly and clean up
tar -xzf server.tar.gz --strip-components=1
rm server.tar.gz
# Verify the server entry point exists
ls -la node
exit
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d laqma.testdevelopment.top
dig +short laqma.testdevelopment.top
dig @8.8.8.8 +short laqma.testdevelopment.top
dig +short laqma.testdevelopment.top
ip a
dig +short laqma.testdevelopment.top
sudo certbot --nginx -d laqma.testdevelopment.top
bassem@srv1928909:~$ dig @8.8.8.8 +short laqma.testdevelopment.top
bassem@srv1928909:~$ 
dig +short laqma.testdevelopment.top
dig @8.8.8.8 +short laqma.testdevelopment.top
sudo certbot --nginx -d laqma.testdevelopment.top
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
sudo systemctl restart nginx
sudo certbot --nginx -d laqma.testdevelopment.top
sudo certbot renew --dry-run
