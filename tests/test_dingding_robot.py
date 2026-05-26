from utils.dingding_robot import DingdingRobot


def test_dingding_requires_configuration(monkeypatch):
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DINGTALK_ACCESS_TOKEN", raising=False)

    robot = DingdingRobot()

    assert robot.send_text("hello") == {"error": "DingTalk webhook is not configured"}


def test_dingding_uses_access_token_without_hardcoded_url():
    robot = DingdingRobot(access_token="abc")

    assert "access_token=abc" in robot.url
    assert "4c0a32040b7251d2caf8bb66a8b4ab823d25ad16dc1725f34653b48176d185f8" not in robot.url
