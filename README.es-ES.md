

# Dnsmasq WebConf

Interfaz web de configuración sencilla para Dnsmasq

![Screenshot](https://user-images.githubusercontent.com/4126355/70373854-96a87080-192f-11ea-8c5e-673323248b6d.png)

## Instalación

* clonar el repositorio

```shell
$ mkdir -p ~/git && cd ~/git
$ git clone git@github.com:akivajp/dnsmasq-webconf.git
```

* instalar los módulos de pip 

```
$ pip install --user bottle jinja2
```

## Uso

* iniciar el servidor

```
$ python ~/git/dnsmasq-webconf/app/index.py [port_number] [--hosts path_to_hosts_file] [--leases path_to_leases_file --config] [path_to_dnsmasq_config]
```

* acceder con tu navegador: `http://hostname:port_number`
