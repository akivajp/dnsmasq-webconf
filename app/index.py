#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime
import json
import os
import pprint
import sys

import bottle
from bottle import request
from bottle import response
from bottle import route
from bottle import static_file
from bottle import jinja2_template as template
from bottle import TEMPLATE_PATH

os.environ["DEBUG"] = "1"
from lpu.common import logging
logger = logging.getColorLogger(__name__)
def dprint(obj):
    logger.debug_print(pprint.pformat(obj), offset=1)
    #logger.debug_print(pprint.pformat(obj))

#TEMPLATE_PATH.append("./views")
dirpath = os.path.dirname(os.path.abspath(__file__))
sys.path.append(dirpath)
#os.chdir(dirpath)
TEMPLATE_PATH.append(os.path.join(dirpath, 'views'))
#repodir = os.path.dirname(dirpath)
static_dir = os.path.join(dirpath, 'static')

DEFAULT_HOSTS = '/etc/hosts'
DEFAULT_LEASES = '/var/lib/dnsmasq/dnsmasq.leases'
DEFAULT_CONFIG = '/etc/dnsmasq.conf'

hosts_file = None
leases_file = None
config_file = None

def get_hosts():
    if not hosts_file:
        return None
    if not os.path.isfile(hosts_file):
        return None
    hosts = []
    with open(hosts_file) as fobj:
        for line in fobj:
            line = line.strip()
            #dprint(line)
            if line and line[0] != '#':
                fields = line.split()
                if len(fields) >= 2:
                    host = {}
                    names = []
                    host['num'] = len(hosts) + 1
                    host['addr'] = fields[0]
                    for field in fields[1:]:
                        field = field.strip()
                        if field.startswith('#'):
                            # commented-out after "#""
                            break
                        names.append(field)
                    host['names'] = names
                    if names:
                        hosts.append(host)
    return hosts

def get_leases():
    if not leases_file:
        return None
    if not os.path.isfile(leases_file):
        return None
    hosts = []
    with open(leases_file) as fobj:
        for line in fobj:
            line = line.strip()
            #dprint(line)
            fields = line.split()
            if len(fields) >= 5:
                host = {}
                host['num'] = len(hosts) + 1
                host['epoch'] = fields[0]
                dt = datetime.datetime.fromtimestamp(float(fields[0]))
                host['timestamp'] = str(dt)
                host['mac'] = fields[1]
                host['addr'] = fields[2]
                host['name'] = fields[3]
                host['id'] = fields[4]
                hosts.append(host)
    return hosts

def get_config():
    if not config_file:
        return None
    if not os.path.isfile(config_file):
        return None
    #hosts = []
    config = {}
    config['hosts'] = hosts = []
    config['ignored_hosts'] = ignored_hosts = []
    #lines = []
    #config['lines'] = lines
    with open(config_file) as fobj:
        for i, line in enumerate(fobj):
            #lines.append(line)
            line = line.strip()
            config['num_lines'] = i + 1
            #dprint(line)
            commented = False
            if line[:1] == "#":
                commented = True
                line = line[1:].strip()
            #dprint(commented)
            #dprint(line)
            if line.startswith('dhcp-host='):
                host = {}
                remain = line[line.find('=')+1:]
                if '#' in remain:
                    pos = remain.find('#')
                    host['comment'] = remain[pos+1:].strip()
                    remain = remain[:pos]
                fields = remain.split(',')
                #dprint(fields)
                mac_list = []
                extra_list = []
                for field in fields:
                    field = field.strip()
                    #dprint(field)
                    #if field.find('id:') == 0:
                    #    host['id'] = field[field.find(':')+1:]
                    if len(field.split(':')) == 6:
                        #if field.split(':')[0].isdigit():
                        mac_list.append(field)
                            #host['mac'] = mac_list
                    elif len(field.split('.')) == 4:
                        if field.split('.')[0].isdigit():
                            host['addr'] = field
                    else:
                        if field == 'infinite':
                            host['lease'] = field
                        elif field == 'ignore':
                            host['ignore'] = True
                        elif field[:1].isdigit():
                            host['lease'] = field
                        elif field.isalnum():
                            host['name'] = field
                        else:
                            extra_list.append(field)
                            #host['extra'] = extra_list
                if host:
                    host['line_num'] = i+1
                    host['line'] = line
                    #host['commented'] = commented
                    host['valid'] = not commented
                    host['mac'] = mac_list
                    host['extra'] = extra_list
                    if host.get('ignore'):
                        host['num'] = len(ignored_hosts) + 1
                        ignored_hosts.append(host)
                    else:
                        host['num'] = len(hosts) + 1
                        hosts.append(host)
                #dprint(host)
    return config

@route('/')
def index():
    hosts = get_hosts()
    leases = get_leases()
    config = get_config()
    #dprint(config)
    context = dict(
        #hosts = hosts,
        hosts_json = json.dumps(hosts),
        hosts_file = hosts_file,
        leases_json = json.dumps(leases),
        leases_file = leases_file,
        config_json = json.dumps(config),
    )
    return template('main.html.j2', context)

@route('/static/<path>')
def static(path):
    return static_file(path, root=static_dir)

def host_to_line(host):
    #line = ""
    fields = []
    extra_list = host.get('extra', [])
    if extra_list:
        new_extra_list = []
        for entry in extra_list:
            if entry.startswith('id:'):
                fields.append(entry)
            else:
                new_extra_list.append(entry)
        extra_list = new_extra_list
    mac_list = host.get('mac', [])
    if mac_list:
        #line += str.join(',', mac_list)
        fields.append(str.join(',', mac_list))
    name = host.get('name', None)
    if name:
        fields.append(name)
    addr = host.get('addr', None)
    if addr:
        fields.append(addr)
    if extra_list:
        #fields.append(extra)
        fields.append(str.join(', ', extra_list))
    duration = host.get('lease', None)
    if duration:
        fields.append(duration)
    ignore = host.get('ignore', None)
    if ignore:
        fields.append('ignore')
    head = ""
    delete = host.get('delete', False)
    valid = host.get('valid', True)
    if delete:
        head += '##'
    elif not valid or len(fields) == 1:
        head += '#'
    comment = host.get('comment', '')
    if comment:
        comment = ' # ' + comment
    head += 'dhcp-host='
    return head + str.join(', ', fields) + comment

@route('/api/save', method=["GET", "POST"])
def save():
    data = request.json
    #dprint(data)
    if os.path.isfile(config_file):
        lines = open(config_file, 'r').readlines()
    else:
        lines = []
    for host in data['hosts'] + data['ignored_hosts']:
        #dprint(host)
        line_num = host.get('line_num', None)
        #dprint(line_num)
        if host.get('changed', False):
            if host.get('appended', False):
                lines.append(host_to_line(host) + "\n")
            elif line_num is not None:
                if 1 <= line_num and line_num <= len(lines):
                    line = lines[line_num-1]
                else:
                    line = ""
                if line.find(host['line']) >= 0:
                    lines[line_num-1] = host_to_line(host) + "\n"
            else:
                raise RuntimeError("invalid path")
            dprint(host_to_line(host))
    with open(config_file, 'w') as fobj:
        fobj.writelines(lines)
    result = {}
    result["status"] = "OK"
    response.headers['Content-Type'] = 'application/json'
    response.headers['Cache-Control'] = 'no-cache'
    return json.dumps(result)

dprint(__name__)
if __name__ == '__main__':
    parser = argparse.ArgumentParser('Dnsmasq Configurator')
    parser.add_argument('port', default=80, nargs='?')
    parser.add_argument('--hosts', '-H', type=str, default=DEFAULT_HOSTS)
    parser.add_argument('--leases', '-L', type=str, default=DEFAULT_LEASES)
    parser.add_argument('--config', '-C', type=str, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    dprint(args)
    if args.hosts:
        hosts_file = args.hosts
        leases_file = args.leases
        config_file = args.config
    bottle.run(host='0.0.0.0', port=args.port, quite=False, debug=True, reloader=True)
