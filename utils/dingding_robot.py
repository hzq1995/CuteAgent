import json
import os
from urllib.parse import urlencode

import requests


DEFAULT_DINGTALK_SEND_URL = "https://oapi.dingtalk.com/robot/send"


class DingdingRobot:
    def __init__(self, webhook_url=None, access_token=None):
        self.url = (
            webhook_url
            or self._url_from_token(access_token)
            or os.getenv("DINGTALK_WEBHOOK_URL")
            or self._url_from_token(os.getenv("DINGTALK_ACCESS_TOKEN"))
        )
        self.headers = {"Content-Type": "application/json"}

    @staticmethod
    def _url_from_token(access_token):
        if not access_token:
            return ""
        return f"{DEFAULT_DINGTALK_SEND_URL}?{urlencode({'access_token': access_token})}"

    def send_markdown(self, title, text, at_mobiles=None, at_user_ids=None, is_at_all=False):
        if not self.url:
            return {"error": "DingTalk webhook is not configured"}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "atUserIds": at_user_ids or [],
                "isAtAll": is_at_all,
            },
        }

        try:
            response = requests.post(self.url, headers=self.headers, data=json.dumps(data), timeout=20)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def send_text(self, content, at_mobiles=None, at_user_ids=None, is_at_all=False):
        if not self.url:
            return {"error": "DingTalk webhook is not configured"}
        data = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "atUserIds": at_user_ids or [],
                "isAtAll": is_at_all,
            },
        }

        try:
            response = requests.post(self.url, headers=self.headers, data=json.dumps(data), timeout=20)
            return response.json()
        except Exception as e:
            return {"error": str(e)}


if __name__ == "__main__":
    robot = DingdingRobot()
    result = robot.send_text("CuteHarness DingTalk test")
    print("Send result:", result)
