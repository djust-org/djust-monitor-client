from djust_monitor.scrubber import FILTERED, scrub


class TestScrubber:
    def test_scrubs_password(self):
        data = {"username": "alice", "password": "secret123"}
        scrub(data)
        assert data["username"] == "alice"
        assert data["password"] == FILTERED

    def test_scrubs_api_key(self):
        data = {"api_key": "sk-12345", "name": "test"}
        scrub(data)
        assert data["api_key"] == FILTERED
        assert data["name"] == "test"

    def test_scrubs_authorization_header(self):
        data = {"headers": {"Authorization": "Bearer tok123", "Content-Type": "application/json"}}
        scrub(data)
        assert data["headers"]["Authorization"] == FILTERED
        assert data["headers"]["Content-Type"] == "application/json"

    def test_scrubs_nested_dicts(self):
        data = {
            "request": {
                "headers": {
                    "Cookie": "session=abc",
                    "Host": "example.com",
                }
            }
        }
        scrub(data)
        assert data["request"]["headers"]["Cookie"] == FILTERED
        assert data["request"]["headers"]["Host"] == "example.com"

    def test_scrubs_in_lists(self):
        data = {"items": [{"secret_key": "hidden", "value": "visible"}]}
        scrub(data)
        assert data["items"][0]["secret_key"] == FILTERED
        assert data["items"][0]["value"] == "visible"

    def test_case_insensitive(self):
        data = {"PASSWORD": "secret", "Session_ID": "abc"}
        scrub(data)
        assert data["PASSWORD"] == FILTERED
        assert data["Session_ID"] == FILTERED

    def test_preserves_non_sensitive(self):
        data = {
            "method": "POST",
            "url": "https://example.com/",
            "user_id": 42,
        }
        scrub(data)
        assert data["method"] == "POST"
        assert data["url"] == "https://example.com/"
        assert data["user_id"] == 42

    def test_handles_empty_dict(self):
        data = {}
        scrub(data)
        assert data == {}

    def test_handles_deeply_nested(self):
        data = {"a": {"b": {"c": {"token": "xyz"}}}}
        scrub(data)
        assert data["a"]["b"]["c"]["token"] == FILTERED

    def test_credit_card_and_ssn(self):
        data = {"credit_card": "4111111111111111", "ssn": "123-45-6789"}
        scrub(data)
        assert data["credit_card"] == FILTERED
        assert data["ssn"] == FILTERED
