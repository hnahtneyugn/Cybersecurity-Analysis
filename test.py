import sys
import certstream

def message_callback(message, context):
    if message['message_type'] == 'certificate_update':
        domains = message['data']['leaf_cert']['all_domains']
        for d in domains:
            print(d)
        sys.stdout.flush()

print("Connecting to CertStream...")
certstream.listen_for_events(message_callback, url='ws://0.0.0.0:8080/')