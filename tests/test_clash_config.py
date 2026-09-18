import unittest

import yaml

from deploy import build_clash_yaml


class ClashConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load(
            build_clash_yaml("192.0.2.10", "test-password")
        )

    def route_for(self, domain):
        for rule in self.config["rules"]:
            fields = rule.split(",")
            if fields[0] == "MATCH":
                return fields[1]
            kind, target, policy = fields
            if kind == "DOMAIN" and domain == target:
                return policy
            if kind == "DOMAIN-SUFFIX" and (
                domain == target or domain.endswith("." + target)
            ):
                return policy
        self.fail("No matching rule")

    def test_openai_pages_and_challenges_use_one_selectable_route(self):
        for domain in [
            "openai.com",
            "auth.openai.com",
            "chatgpt.com",
            "ab.chatgpt.com",
            "persistent.oaistatic.com",
            "files.oaiusercontent.com",
            "challenges.cloudflare.com",
            "cdn.auth0.com",
        ]:
            with self.subTest(domain=domain):
                self.assertEqual(self.route_for(domain), "OpenAI")

    def test_openai_does_not_force_unverified_direct_access(self):
        groups = {group["name"]: group for group in self.config["proxy-groups"]}
        self.assertIn("OpenAI", groups)
        self.assertEqual(groups["OpenAI"]["type"], "select")
        self.assertEqual(groups["OpenAI"]["proxies"], ["DIRECT", "Proxy"])
        self.assertEqual(groups["Proxy"]["proxies"][0], "192.0.2.10 Shadowsocks")

    def test_unrelated_websites_keep_the_regular_proxy_route(self):
        for domain in [
            "example.com",
            "other.auth0.com",
            "other.sentry.io",
            "openai.com.example.org",
            "notopenai.com",
        ]:
            with self.subTest(domain=domain):
                self.assertEqual(self.route_for(domain), "Proxy")

    def test_password_round_trips_without_yaml_injection(self):
        password = 'quote"\\value\nrules: [injected]'
        config = yaml.safe_load(build_clash_yaml("192.0.2.10", password))
        self.assertEqual(config["proxies"][0]["password"], password)
        self.assertEqual(config["rules"][-1], "MATCH,Proxy")


if __name__ == "__main__":
    unittest.main()
