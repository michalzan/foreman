from types import SimpleNamespace

import tunnels

LSOF = "\n".join([
    "p710", "cControlCenter", "f9", "n*:7000",
    "p49542", "cssh", "f10", "n*:5433", "f13", "n*:9092", "f14", "n[::1]:9092",
    "p51000", "cssh", "f3", "n127.0.0.1:8090",
    "p52000", "ckubectl", "f7", "n127.0.0.1:15432",
])
PS = {
    "49542": "ssh: /Users/x/.colima/_lima/colima/ssh.sock [mux]",
    "51000": "ssh -N -L 8090:edge.example.com:80 prod-tunnel",
    "52000": "kubectl port-forward svc/db 15432:5432",
}


def fake(cmd, **kw):
    if cmd[0] == "lsof":
        return SimpleNamespace(stdout=LSOF)
    return SimpleNamespace(stdout=PS.get(cmd[-1], ""))


def test_tunnels_are_listed_and_the_docker_vm_is_not():
    assert tunnels.held_ports(fake) == [
        (8090, "ssh -N -L 8090:edge.example.com:80 prod-tunnel"),
        (15432, "kubectl port-forward svc/db 15432:5432"),
    ]


def test_the_warning_names_each_port_and_says_where_traffic_goes():
    text = tunnels.warning(fake)
    assert "8090 (ssh -N -L 8090" in text and "15432" in text and "leaves this machine" in text
    assert "5433" not in text and "7000" not in text


def test_no_lsof_means_no_warning():
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert tunnels.held_ports(missing) == [] and tunnels.warning(missing) == ""
