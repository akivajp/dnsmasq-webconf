# Dnsmasq WebConf

Simple Configuration Web UI for Dnsmasq

![Screenshot](https://user-images.githubusercontent.com/4126355/70373854-96a87080-192f-11ea-8c5e-673323248b6d.png)

## Installation

* clone repository

```shell
$ mkdir -p ~/git && cd ~/git
$ git clone git@github.com:akivajp/dnsmasq-webconf.git
```

* install pip modules 

```
$ pip install --user bottle jinja2 numpy lpu
```

## Usage

* start server

```
$ python ~/git/dnsmasq-webconf/app/index.py [port_number] [--hosts path_to_hosts_file] [--leases path_to_leases_file --config] [path_to_dnsmasq_config]
```

* access with your browser: `http://hostname:port_number`
