#!/usr/bin/env bash
#for ubuntu 14
#this requires root access
if $(lsb_release -d | grep -q "14"); then
    echo "deb https://apt.dockerproject.org/repo ubuntu-trusty main" > /etc/apt/sources.list.d/docker.list
fi

# for ubuntu 15
if $(lsb_release -d | grep -q "15"); then
    echo "deb https://apt.dockerproject.org/repo ubuntu-wily main" > /etc/apt/sources.list.d/docker.list
fi

# for linux mint
if $(lsb_release -d | grep -q "Mint"); then
    echo "deb https://apt.dockerproject.org/repo ubuntu-trusty main" > /etc/apt/sources.list.d/docker.list
fi

apt-key adv --keyserver hkp://p80.pool.sks-keyservers.net:80 --recv-keys 58118E89F3A912897C070ADBF76221572C52609D