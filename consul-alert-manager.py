#!/usr/bin/env python3

import requests
import json
import os
import consul
import smtplib
import argparse
from time import sleep
from email.message import EmailMessage

# Default var's values

log_file = '/var/log/consul-alert-manager.log'

smtp_host = 'localhost'
smtp_sender = 'alert-manager@localhost'
smtp_reciever = 'example@example.com'

consul_scheme = 'http'
consul_host = '127.0.0.1'
consul_port = 8500

alert_manager_key_prefix = 'alert-manager'

def log(message):
    with open(log_file, 'a') as f:
        f.write(str(message + "\n"))

def get_args():
    parser = argparse.ArgumentParser(description='Send email notifications in case of failing Consul checks.', usage='%(prog)s [options]')
    parser.add_argument('--log-file', nargs='?', default=log_file, metavar='value', help='path to log file', dest='log_file') 
    parser.add_argument('--smtp-host', nargs='?', default=smtp_host, metavar='value', help='SMTP host', dest='smtp_host')               
    parser.add_argument('--smtp-reciever', nargs='?', default=smtp_reciever, metavar='value', help='SMTP email receiver', dest='smtp_reciever')
    parser.add_argument('--smtp-sender', nargs='?', default=smtp_sender, metavar='value', help='SMTP email sender', dest='smtp_sender')
    
    return parser.parse_args()


def get_kv_value(k):
    assert k != ''
    _, v = c.kv.get(k)
    if v is not None:
        v = v['Value'].decode('ascii')
    return v


def delete_key(k):
    assert k != ''
    if c.kv.delete(k, recurse=True):
        log('[INFO] Deleted key: ' + k)
        return True
    return False


def send_email(header, dc, node, service, check_id, state, output):
    try:
        msg = EmailMessage()
        content = "DC: {0}\nNode: {1}\nService: {2}\nCheckID: {3}\n\nOutput:\n{4}".format(dc, node, service, check_id, output)
        msg.set_content(content)
        msg['Subject'] = "{0}: {2}: {1}".format(header, check_id, state)
        msg['From'] = smtp_sender
        msg['To'] = smtp_reciever

        s = smtplib.SMTP(smtp_host)
        s.send_message(msg)
        s.quit()
        
        log('[INFO] Email was sent to ' + smtp_reciever)
    except:
        log("[ERROR] Email not sent")


def get_output_by_check_id(dc, node, check_id):
    _, services = c.health.node(node, dc=dc)
    for n in services:
        if (n['CheckID'] == check_id):
            return n['Output']


def is_check_resolved(dc, node, check_id, target_state):
    _, services = c.health.node(node, dc=dc)
    for n in services:
        if (n['CheckID'] == check_id) and (n['Status'] == target_state):
            return True

    return False

def is_check_present(dc, node, check_id):
    _, services = c.health.node(node, dc=dc)
    for n in services:
        if (n['CheckID'] == check_id):
            return True

    return False


def handle_saved_states(saved_states):
    for state in saved_states:
        _, keys = c.kv.get(alert_manager_key_prefix +'/' + state, keys=True)
        if keys is not None:
            for k in keys:
                data = k.split('/')
                dc, node, check_id = data[2], data[3], data[4]

                target_state = 'passing'
                
                if not is_check_present(dc, node, check_id):
                    log('[INFO] Previously saved checkid ' + check_id + ' is absent')
                    delete_key(k)
                    continue
                    
                if is_check_resolved(dc, node, check_id, target_state):
                    service, output = '', ''
                    if (len(data) > 5):
                        service = data[5]
                        output = get_output_by_check_id(dc, node, check_id)
                
                    log('[OK] Found ' + target_state + ' state: ' + check_id)
                    if (delete_key(k)):
                        send_email('Resolved', dc, node, service, check_id, target_state, output)


def handle_novel_states(states):
    for dc in datacenters:
        for state in states:
            _, services = c.health.state(state, dc=dc)
            for s in services:
                node, service, output, check_id = s['Node'], s['ServiceName'], s['Output'], s['CheckID']
                k = "{0}/{1}/{2}/{3}/{4}/{5}".format(alert_manager_key_prefix, state , dc, node, check_id, service).rstrip('/')

                _, v = c.kv.get(k)
                if v is None:
                    if (c.kv.put(k, output)):
                        log('[WARN] Found ' + state + ' state. Saved state key: ' + k)
                        send_email('Problem', dc, node, service, check_id, state, output)


def wait_for_connection():
    try:
        consul.Consul(host=consul_host, port=consul_port, scheme=consul_scheme).catalog.datacenters()
        log('[OK] Connection restored. Consul Alert Manager is ready')
    except requests.exceptions.ConnectionError:
        sleep(10)
        wait_for_connection()


if __name__ == '__main__':
    args = get_args()

    if args.log_file is not None:
        log_file = args.log_file
    if args.smtp_host is not None:
        smtp_host = args.smtp_host
    if args.smtp_reciever is not None:
        smtp_reciever = args.smtp_reciever
    if args.smtp_sender is not None:
        smtp_sender = args.smtp_sender

    log('[OK] Consul Alert Manager is started')

    while True:
        try:
            c = consul.Consul(host=consul_host, port=consul_port, scheme=consul_scheme)

            # Get datacenters list
            datacenters = c.catalog.datacenters()

            # Alerting for the next states 
            processing_states = ['warning', 'critical']

            handle_saved_states(processing_states)
            handle_novel_states(processing_states)
        except requests.exceptions.ConnectionError:
            log('[ERROR] Connection error with Consul on ' + consul_scheme + '://' + consul_host + ':' + str(consul_port) + '. Reconnecting...')
            wait_for_connection()
        except Exception as e:
            log('[ERROR] Program crashed')
            send_email('Crashed', '', '', 'Consul Alert Manager', 'Consul Alert Manager', 'crashed', str(e))
            raise

        sleep(10)

