#!/usr/bin/env python3
"""Shared lab Sysmon profile includes python/nmap/kerbrute and drops Kaspersky."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ATTACKER_XML = REPO / "attacker" / "sysmonconfig.xml"
ROOT_XML = REPO / "sysmonconfig.xml"


def _block(text: str, start_tag: str, end_tag: str) -> str:
    start = text.find(start_tag)
    end = text.find(end_tag, start)
    if start < 0 or end < 0:
        return ""
    return text[start:end]


def _network_connect_block(text: str) -> str:
    return _block(text, '<NetworkConnect onmatch="include">', "</NetworkConnect>")


def _process_access_exclude(text: str) -> str:
    return _block(text, '<ProcessAccess onmatch="exclude">', "</ProcessAccess>")


def _process_access_include(text: str) -> str:
    return _block(text, '<ProcessAccess onmatch="include">', "</ProcessAccess>")


class AttackerSysmonConfigTests(unittest.TestCase):
    def test_attacker_copy_has_tool_rules(self) -> None:
        text = ATTACKER_XML.read_text(encoding="utf-8")
        self.assertIn("HolyFW Sysmon config for all lab hosts", text)
        self.assertIn('name="holyfw_attacker_tool"', text)
        for image in ("python.exe", "pythonw.exe", "python3.exe", "py.exe", "nmap.exe", "kerbrute.exe"):
            self.assertIn(f'condition="image">{image}</Image>', text)
        for needle in ("impacket", "bloodhound", "nmap", "kerbrute"):
            self.assertIn(f'condition="contains">{needle}</CommandLine>', text)
        network = _network_connect_block(text)
        self.assertIn('condition="image">python.exe</Image>', network)
        self.assertIn('condition="image">nmap.exe</Image>', network)
        self.assertIn('condition="image">kerbrute.exe</Image>', network)
        self.assertNotIn('DestinationPort name="holyfw_attacker_tool"', network)

    def test_excludes_kaspersky_process_access(self) -> None:
        text = ATTACKER_XML.read_text(encoding="utf-8")
        include = _process_access_include(text)
        exclude = _process_access_exclude(text)
        self.assertIn("lsass.exe", include)
        self.assertIn(r'condition="contains">\Kaspersky Lab\</SourceImage>', exclude)
        self.assertIn('condition="image">avp.exe</SourceImage>', exclude)
        self.assertIn('condition="image">avpui.exe</SourceImage>', exclude)
        self.assertIn('name="holyfw_exclude_kaspersky"', text)

    def test_root_profile_does_not_include_python_network(self) -> None:
        text = ROOT_XML.read_text(encoding="utf-8")
        self.assertNotIn("holyfw_attacker_tool", text)
        network = _network_connect_block(text)
        self.assertNotIn('condition="image">python.exe</Image>', network)
        self.assertNotIn('condition="image">nmap.exe</Image>', network)
        self.assertNotIn('condition="image">kerbrute.exe</Image>', network)


if __name__ == "__main__":
    unittest.main()
