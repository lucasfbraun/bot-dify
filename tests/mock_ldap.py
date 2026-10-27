"""AD falso (ldap3 MOCK_SYNC) só para testes locais. Senha de todos: Teste@123"""
from ldap3 import MOCK_SYNC, Connection, Server

SENHA = "Teste@123"
BASE = "DC=flexivel,DC=local"
PESSOAS = [
    ("svc.integracao", "Integração TI", "", "TI", "Conta de serviço", 512),
    ("admin.ti", "Admin da TI", "admin.ti@grupoflexivel.com.br", "Tecnologia da Informação", "Analista de Sistemas", 512),
    ("maria.silva", "Maria Silva", "maria.silva@grupoflexivel.com.br", "Engenharia de Processos", "Engenheira Química", 512),
    ("carlos.souza", "Carlos Souza", "carlos.souza@grupoflexivel.com.br", "Qualidade", "Coordenador da Qualidade", 512),
    ("joao.desligado", "João Desligado", "joao@grupoflexivel.com.br", "Comercial", "Vendedor", 514),
]


def criar_servidor() -> Server:
    srv = Server("ad-falso")
    c = Connection(srv, user="cn=root", password="x", client_strategy=MOCK_SYNC)
    c.strategy.add_entry("cn=root", {"userPassword": "x", "sn": "root"})
    for sam, nome, mail, dep, cargo, uac in PESSOAS:
        dn = f"CN={nome},OU=Usuarios,{BASE}"
        upn = f"{sam}@flexivel.local"
        attrs = {"objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": "person",
                 "sAMAccountName": sam, "displayName": nome, "givenName": nome.split()[0], "sn": nome.split()[-1],
                 "mail": mail, "department": dep, "title": cargo, "userAccountControl": str(uac),
                 "userPrincipalName": upn, "distinguishedName": dn, "userPassword": SENHA}
        c.strategy.add_entry(dn, attrs)
        # o bind do código usa o UPN; no mock o bind procura a chave no "dit"
        srv.dit[upn] = srv.dit[dn]
    return srv
