from http.cookies import SimpleCookie

from probe_webview_login import (cookie_summary, has_trust_cookie, has_webauth,
                                 to_httpx_cookies)


def make(name, value, domain=".icloud.com", path="/"):
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["domain"] = domain
    cookie[name]["path"] = path
    return cookie


COOKIES = [
    make("X-APPLE-WEBAUTH-USER", "SECRET-ONE"),
    make("X-APPLE-WEBAUTH-TOKEN", "SECRET-TWO", domain="www.icloud.com"),
    make("X-APPLE-WEBAUTH-HSA-TRUST", "SECRET-THREE"),
]


def test_summary_has_names_and_domains_but_never_values():
    text = cookie_summary(COOKIES)
    assert "X-APPLE-WEBAUTH-USER" in text
    assert ".icloud.com" in text
    assert "www.icloud.com" in text
    for secret in ("SECRET-ONE", "SECRET-TWO", "SECRET-THREE"):
        assert secret not in text


def test_summary_of_nothing():
    assert cookie_summary([]) == "(žiadne cookies)"


def test_detects_webauth_and_trust_case_insensitively():
    assert has_webauth(COOKIES)
    assert has_trust_cookie(COOKIES)
    assert not has_webauth([make("other", "x")])
    assert has_trust_cookie([make("x-apple-hsa-Trust", "x")])
    assert not has_trust_cookie([make("X-APPLE-WEBAUTH-USER", "x")])


def test_httpx_jar_keeps_values_and_domains():
    jar = to_httpx_cookies(COOKIES)
    assert jar.get("X-APPLE-WEBAUTH-USER", domain=".icloud.com") == "SECRET-ONE"
    assert jar.get("X-APPLE-WEBAUTH-TOKEN", domain="www.icloud.com") == "SECRET-TWO"
    assert len(list(jar.jar)) == 3
