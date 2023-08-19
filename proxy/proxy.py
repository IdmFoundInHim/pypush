import sys
sys.path.append("../")
sys.path.append("../../")

import apns
import trio
import ssl

import logging
from rich.logging import RichHandler
from hashlib import sha1
import plistlib
import gzip


logging.basicConfig(
    level=logging.NOTSET,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)

async def main():
    apns.COURIER_HOST = "windows.courier.push.apple.com" # Use windows courier so that /etc/hosts override doesn't affect it

    context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
    context.set_alpn_protocols(["apns-security-v3"])
    # Set the certificate and private key
    context.load_cert_chain("push_certificate_chain.pem", "push_key.pem")
    
    await trio.serve_ssl_over_tcp(handle_proxy, 5223, context)

async def handle_proxy(stream: trio.SocketStream):
    try:
        p = APNSProxy(stream)
        await p.start()
    except Exception as e:
        logging.error("APNSProxy instance encountered exception: " + str(e))
        #raise e

class APNSProxy:
    def __init__(self, client: trio.SocketStream):
        self.client = client

    async def start(self):
        async with trio.open_nursery() as nursery:
            apns_server = apns.APNSConnection(nursery)
            await apns_server._connect_socket()
            self.server = apns_server.sock

            nursery.start_soon(self.proxy, True)
            nursery.start_soon(self.proxy, False)
    

    async def proxy(self, to_server: bool):
        if to_server:
            from_stream = self.client
            to_stream = self.server
        else:
            from_stream = self.server
            to_stream = self.client
        while True:
            payload = await apns.APNSPayload.read_from_stream(from_stream)
            payload = self.tamper(payload, to_server)
            self.log(payload, to_server)
            await payload.write_to_stream(to_stream)

    def log(self, payload: apns.APNSPayload, to_server: bool):
        import printer
        printer.print_payload(payload, to_server)
        # if to_server:
        #     logging.info(f"-> {payload}")
        # else:
        #     logging.info(f"<- {payload}")
        
    def tamper(self, payload: apns.APNSPayload, to_server) -> apns.APNSPayload:
        #if not to_server:
        #    payload = self.tamper_lookup_keys(payload)

        return payload

    def tamper_lookup_keys(self, payload: apns.APNSPayload) -> apns.APNSPayload:
        if payload.id == 0xA: # Notification
            if payload.fields_with_id(2)[0].value == sha1(b"com.apple.madrid").digest(): # Topic
                if payload.fields_with_id(3)[0].value is not None: # Body
                    body = payload.fields_with_id(3)[0].value
                    body = plistlib.loads(body)
                    if body['c'] == 97: # Lookup response
                        resp = gzip.decompress(body["b"]) # HTTP body
                        resp = plistlib.loads(resp)

                        # Replace public keys
                        for r in resp["results"].keys():
                            for i in range(len(resp["results"][r]["identities"])):
                                if "client-data" in resp["results"][r]["identities"][i]:
                                    resp["results"][r]["identities"][i]["client-data"]["public-message-identity-key"] = b"REDACTED"
                        
                        resp = gzip.compress(plistlib.dumps(resp, fmt=plistlib.FMT_BINARY), mtime=0)
                        body["b"] = resp
                    body = plistlib.dumps(body, fmt=plistlib.FMT_BINARY)
                    for f in range(len(payload.fields)):
                        if payload.fields[f].id == 3:
                            payload.fields[f].value = body
                            break
        return payload

if __name__ == "__main__":
    trio.run(main)