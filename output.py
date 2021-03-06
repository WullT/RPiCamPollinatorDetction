import json
import datetime
from PIL import Image
from io import BytesIO
import base64
from dataclasses import dataclass
import os
import sys

import logging
import ssl
import requests

log = logging.getLogger(__name__)
log.propagate = False
log.setLevel(logging.INFO)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(
    logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
)
log.addHandler(handler)

DECIMALS_TO_ROUND = 3


class Message:
    def __init__(self, ts, node_id):
        self.flowers = []
        self.pollinators = []
        self.timestamp = ts
        self.metadata = {}
        self.node_id = node_id

    def add_flower(self, index, class_name, score, width, height):
        self.flowers.append(
            {
                "index": index,
                "class_name": class_name,
                "score": round(float(score), DECIMALS_TO_ROUND),
                "width": width,
                "height": height,
            }
        )

    def add_pollinator(self, index, flower_index, class_name, score, crop=None):
        pollinator = {
            "index": index,
            "flower_index": flower_index,
            "class_name": class_name,
            "score": round(float(score), DECIMALS_TO_ROUND),
        }
        if crop is not None:
            bio = BytesIO()
            crop.save(bio, format="JPEG")
            pollinator["crop"] = base64.b64encode(bio.getvalue()).decode("utf-8")
        self.pollinators.append(pollinator)

    def add_metadata(
        self, flowermeta, pollimeta, input_image_size, capture_duration, img_source
    ):
        self.metadata["node_id"] = self.node_id
        self.metadata["capture_timestamp"] = str(self.timestamp)
        self.metadata["original_image"] = {
            "size": input_image_size,
            "capture_duration": round(capture_duration, DECIMALS_TO_ROUND),
            "source": img_source,
        }

        self.metadata["flower_inference"] = flowermeta
        self.metadata["pollinator_inference"] = pollimeta

    def construct_message(self):
        message = {
            "detections": {
                "flowers": self.flowers,
                "pollinators": self.pollinators,
            },
            "metadata": self.metadata,
        }
        return message


    def generate_filename(self, format=".json"):
        filename = (
            self.node_id + "_" + self.timestamp.strftime("%Y-%m-%dT%H-%M-%SZ") + format
        )
        return filename
    
    def _generate_save_path(self):
        date_dir = self.timestamp.strftime("%Y-%m-%d")
        time_dir = self.timestamp.strftime("%H")
        return self.node_id + "/" + date_dir + "/" + time_dir + "/"

    def store_file(self, base_dir):
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)
        if not base_dir.endswith("/"):
            base_dir += "/"
        filepath = base_dir + self._generate_save_path()
        if not os.path.exists(filepath):
            os.makedirs(filepath)
            log.info("Created directory: {}".format(filepath))
        with open(filepath + self.generate_filename(), "w") as f:
            json.dump(self.construct_message(), f)
        log.info("Saved message to: {}".format(filepath + self.generate_filename()))
        return True



class MQTTClient:
    def __init__(self, host, port, topic, username, password, use_tls):
        self.host = host
        self.port = port
        self.topic = topic
        self.username = username
        self.password = password
        self.use_tls = use_tls
        if self.username is not None and self.password is not None:
            self.auth = {
                "username": self.username,
                "password": self.password,
            }
        else:
            self.auth = None

    def publish(self, message, filename=None, node_id=None, hostname=None):
        import paho.mqtt.publish as publish

        topic = self.topic
        if filename is not None:
            topic = topic.replace("${filename}", filename)
        if node_id is not None:
            topic = topic.replace("${node_id}", node_id)
        if hostname is not None:
            topic = topic.replace("${hostname}", hostname)
        log.info("Publishing to {} on topic: {}".format(self.host, topic))
        tls_config = None
        if self.use_tls:
            tls_config = {
                "certfile": None,
                "keyfile": None,
                "cert_reqs": ssl.CERT_REQUIRED,
                "tls_version": ssl.PROTOCOL_TLSv1_2,
                "ciphers": None,
            }

        publish.single(
            topic,
            json.dumps(message),
            1,
            auth=self.auth,
            hostname=self.host,
            port=self.port,
            tls=tls_config,
        )


class HTTPClient:
    def __init__(self, url, username, password, method="POST"):
        self.url = url
        self.username = username
        self.password = password
        self.method = method
        if self.username is not None and self.password is not None:
            self.auth = (self.username, self.password)
        else:
            self.auth = None

    def send_message(self, message, filename=None, node_id=None, hostname=None):
        headers = {"Content-type": "application/json"}
        url = self.url
        if filename is not None:
            url = url.replace("${filename}", filename)
        if node_id is not None:
            url = url.replace("${node_id}", node_id)
        if hostname is not None:
            url = url.replace("${hostname}", hostname)
        log.info("Sending results to {}".format(url))

        if self.auth is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(
                bytes(self.auth[0] + ":" + self.auth[1], "utf-8")
            ).decode("utf-8")
        try:
            response = requests.request(
                self.method, url, headers=headers, data=json.dumps(message), timeout=10
            )
            if response.status_code == 200:
                log.info("Successfully sent results to {}".format(url))
                return True
            else:
                log.error(
                    "Failed to send results to {}, status code is {}".format(
                        url, response.status_code
                    )
                )
                return False
        except Exception as e:
            log.error(e)
            return False
