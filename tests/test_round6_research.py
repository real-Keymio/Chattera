import unittest
from unittest.mock import patch

from research_tools import _public_ip, _url
from summaries import public_summary


class ResearchBoundaryTests(unittest.TestCase):
    def test_url_rejects_other_hosts_credentials_and_ports(self):
        for url in ("http://arxiv.org/", "https://127.0.0.1/", "https://arxiv.org:444/",
                    "https://user@arxiv.org/", "https://arxiv.org.example.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _url(url)
        with patch("research_tools.socket.getaddrinfo", return_value=[
            (None, None, None, None, ("8.8.8.8", 443)),
            (None, None, None, None, ("127.0.0.1", 443)),
        ]), self.assertRaises(ValueError):
            _public_ip("arxiv.org")

    def test_summary_fails_closed_when_citation_visibility_changes(self):
        source = {"channel": "general", "type": "chat"}
        entry = {"sections": {"unverified": "A hypothesis."}, "source_ids": [7], "author": "user"}
        self.assertIsNotNone(public_summary(entry, "general", {7: source}.get))
        for fields in ({"visibility": ["claude"]}, {"metadata": {"visibility": "private"}},
                       {"channel": "elsewhere"}, {"type": "system"}):
            with self.subTest(fields=fields):
                self.assertIsNone(public_summary(entry, "general", {7: {**source, **fields}}.get))
        self.assertIsNone(public_summary(entry, "general", {}.get))
        self.assertIsNone(public_summary({"source_ids": [7]}, "general", {7: source}.get))
