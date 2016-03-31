#!/bin/bash

get_dependency_software() {
echo "updating apt-get......"
result=$(sudo apt-get update)
if grep -q "Could not resolve" <<< $result; then
    echo "Could not update apt-get, please solve it"
    exit
fi
echo "installing git python-setuptools python-dev python-pip mongodb-server"
result=$(sudo apt-get install -y git python-setuptools python-dev python-pip mongodb-server)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install dependancy software, pls install docker manually"
    exit
fi
echo "installing autoconf libtool tomcat7"
result=$(sudo apt-get install -y autoconf automake libtool tomcat7)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install dependancy software, pls install docker manually"
    exit
fi
}

set_envirement() {
cd /home && sudo rm -rf opentech
sudo mkdir opentech && cd opentech
echo "git cloning open-hackathon source code"
result=$(sudo git clone https://github.com/YaningX/open-hackathon.git)
if grep -q "unable to access" <<< $result; then
    echo "Could not git clone open-hackathon source code, pls check your network"
    exit
fi
cd /home/opentech/open-hackathon
echo "pip is installing required python library"
result=$(sudo pip install -r open-hackathon-server/requirement.txt)

result=$(sudo pip install -r open-hackathon-client/requirement.txt)
sudo cp open-hackathon-server/src/hackathon/config_sample.py open-hackathon-server/src/hackathon/config.py
sudo cp open-hackathon-server/src/hackathon/config_sample.py open-hackathon-server/src/hackathon/config.py
}

get_dependency_for_guacamole() {
echo "installing libcairo2-dev"
result=$(sudo apt-get install -y libcairo2-dev)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install libcairo2-dev for guacamole, pls install guacamole manually"
    exit
fi
echo "installing libjpeg62-dev libpng12-dev libossp-uuid-dev"
result=$(sudo apt-get install -y libjpeg62-dev libpng12-dev libossp-uuid-dev)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install libjpeg62-dev libpng12-dev libossp-uuid-dev for guacamole, pls install guacamole manually"
    exit
fi
echo "installing libfreerdp-dev libpango1.0-dev libssh2-1-dev libtelnet-dev"
result=$(sudo apt-get install -y libfreerdp-dev libpango1.0-dev libssh2-1-dev libtelnet-dev)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install libfreerdp-dev libpango1.0-dev libssh2-1-dev libtelnet-dev for guacamole, pls install guacamole manually"
    exit
fi
echo "installing libvncserver-dev libpulse-dev libwebp-dev libssl-dev libvorbis-dev"
result=$(sudo apt-get install -y libvncserver-dev libpulse-dev libwebp-dev libssl-dev libvorbis-dev)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install libvncserver-dev libpulse-dev libwebp-dev libssl-dev libvorbis-dev for guacamole, pls install guacamole manually"
    exit
fi
}

install_and_config_guacamole() {
# install and Configure guacamole
cd /home/opentech && sudo wget http://sourceforge.net/projects/guacamole/files/current/source/guacamole-server-0.9.9.tar.gz/download
sudo rm -rf guacamole-server-0.9.9
sudo mv download guacamole-server-0.9.9.tar.gz && sudo tar -xzf guacamole-server-0.9.9.tar.gz && cd guacamole-server-0.9.9/
sudo autoreconf -fi
sudo ./configure --with-init-dir=/etc/init.d
sudo make
sudo make install
ldconfig
# configure guacamole client
sudo wget http://sourceforge.net/projects/guacamole/files/current/binary/guacamole-0.9.9.war/download
sudo mv download /var/lib/tomcat7/webapps/guacamole.war
# configure guacamole authentication provider
sudo mkdir /usr/share/tomcat7/.guacamole
sudo mkdir /etc/guacamole
cd /home/opentech/open-hackathon/deploy/guacamole
sudo cp guacamole-sample.properties /etc/guacamole/guacamole.properties
sudo cp *.jar /etc/guacamole
sudo ln -s /etc/guacamole/guacamole.properties /usr/share/tomcat7/.guacamole/guacamole.properties
}


# install docker
install_and_config_docker() {
result=$(sudo apt-get update)
if grep -q "Could not resolve" <<< $result; then
    echo "Could not update apt-get, please solve it"
    exit
fi
result=$(sudo apt-get install apt-transport-https ca-certificates)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install dependancy software for docker, pls install docker manually"
    exit
fi

result=$(sudo apt-get update)
if grep -q "Could not resolve" <<< $result; then
    echo "Could not update apt-get, please solve it"
    exit
fi
result=$(sudo apt-get purge lxc-docker)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install docker, pls install docker manually"
    exit
fi
result=$(sudo apt-cache policy docker-engine)
# for ubuntu 15
result=$(sudo apt-get install -y linux-image-extra-$(uname -r))
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install linux-image-extra-$(uname -r), pls install docker manually"
    exit
fi
# for ubuntu 12 & 14
result=$(sudo apt-get install -y apparmor)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install apparmor, pls install docker manually"
    exit
fi
result=$(sudo apt-get install -y docker-engine)
if grep -q "Unable to lacate" <<< $result; then
    echo "Could not install docker-engine, pls install docker manually"
    exit
fi
sudo service docker start
sudo docker run hello-world

sudo usermod -aG docker ubuntu

sudo groupadd docker
sudo gpasswd -a ${USER} docker

sudo docker pull rastasheep/ubuntu-sshd
}




echo "It may take a long time to install and configure open-hackathon, please wait a moment^_^, ..."
echo "安装将花费一定时间，请耐心等待直到安装完成^_^, ..."


get_dependency_software
set_envirement
get_dependency_for_guacamole
install_and_config_guacamole
install_and_config_docker

# Logging && Hosts
sudo mkdir /var/log/open-hackathon
sudo chmod -R 644 /var/log/open-hackathon

if grep -qs "open-hackathon-dev.chinacloudapp.cn" /etc/hosts; then
    echo "127.0.0.1 open-hackathon-dev.chinacloudapp.cn" >> /etc/hosts
fi

# Installing uWSGI
sudo pip install uwsgi

