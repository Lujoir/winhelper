#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EyeTerm 自建 CA + 服务器证书生成（HTTPS 专项，ADR-032）。

证书体系：
  - 自建根 CA（EyeTerm Internal CA，RSA3072，3650 天）——终端侧内置其公钥
    证书（ca.crt）做 TLS 证书链验证，另以 SHA256 指纹做资源完整性双层校验。
  - 服务器证书（RSA2048，1825 天）—— SAN 必须同时含 DNS:zljtest5.215 与
    IP:172.17.5.215，EKU=serverAuth；终端按 https://172.17.5.215 或
    https://zljtest5.215 访问均能过主机名校验。

模式：
  local  — 本机 cryptography 库生成（开发/测试/冒烟），产出目录自定。
  remote — 生产路径：deploy.py 经 SSH 在服务器用 openssl 生成（私钥不出
           服务器）。本文件只提供等价 openssl 命令序列构造（build_openssl_cmds），
           供 deploy.py 调用与审查。

安全约定：
  - 私钥（ca.key/server.key）0600；ca.key 生产上生成后应导出离线保管，
    服务器留存副本仅为续签便利（内网自建 CA 威胁模型，ADR-032）。
  - 本脚本不打印私钥内容；指纹输出为 CA 公钥证书 SHA256（可安全展示）。
"""
import argparse
import datetime
import ipaddress
import os
import sys

# ---------------------------------------------------------------- 默认身份
DEFAULT_SAN_DNS = "zljtest5.215"
DEFAULT_SAN_IP = "172.17.5.215"
CA_CN = "EyeTerm Internal CA"
CA_DAYS = 3650
SERVER_DAYS = 1825

CA_KEY = "ca.key"
CA_CRT = "ca.crt"
SERVER_KEY = "server.key"
SERVER_CRT = "server.crt"
SERVER_CSR = "server.csr"
CA_CSR = "ca.csr"
FULLCHAIN_CRT = "fullchain.crt"

# 事故教训（2026-09-11 生产部署）：`req -x509` 会叠加发行版 openssl.cnf 的
# 默认 v3_ca 模板与 -addext，产生重复扩展（SKI×3/BC×2）的畸形 CA 证书——
# OpenSSL 1.1.1k(FIPS) 验证链时拒绝（error 20），OpenSSL 3.x 容错放行。
# 因此 CA 一律走 req -new（CSR 不携带扩展）+ x509 -req -signkey 自签 +
# extfile 单一扩展来源；服务器证书同理。fullchain（叶+CA）供服务端发送完整链。


def build_openssl_cmds(san_dns=DEFAULT_SAN_DNS, san_ip=DEFAULT_SAN_IP,
                       out_dir="certs"):
    """生产等价 openssl 命令序列（deploy.py 在服务器执行；供审查/复现）。"""
    subj_srv = "/CN=%s/O=EyeTerm" % san_dns
    ext = (
        "[ext_ca]\n"
        "basicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier=hash\n"
        "\n"
        "[v3_srv]\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:%s,IP:%s\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n" % (san_dns, san_ip))
    cmds = [
        "mkdir -p %s && chmod 700 %s" % (out_dir, out_dir),
        # 扩展文件（CA/服务器单一来源，杜绝模板叠加）
        "cat > %s/ext.cnf <<'ETPEOF'\n%sETPEOF" % (out_dir, ext),
        # 根 CA：CSR（无扩展）→ 自签（ext_ca 单一来源）
        "openssl req -new -newkey rsa:3072 -nodes -sha256 "
        "-keyout %s/%s -out %s/%s -subj '/CN=%s/O=EyeTerm'"
        % (out_dir, CA_KEY, out_dir, CA_CSR, CA_CN),
        "openssl x509 -req -sha256 -days %d -in %s/%s -signkey %s/%s "
        "-out %s/%s -extfile %s/ext.cnf -extensions ext_ca"
        % (CA_DAYS, out_dir, CA_CSR, out_dir, CA_KEY,
           out_dir, CA_CRT, out_dir),
        # 服务器私钥 + CSR
        "openssl req -newkey rsa:2048 -nodes -sha256 "
        "-keyout %s/%s -out %s/%s -subj '%s'"
        % (out_dir, SERVER_KEY, out_dir, SERVER_CSR, subj_srv),
        # CA 签发服务器证书
        "openssl x509 -req -sha256 -days %d "
        "-in %s/%s -CA %s/%s -CAkey %s/%s -CAcreateserial "
        "-out %s/%s -extfile %s/ext.cnf -extensions v3_srv"
        % (SERVER_DAYS, out_dir, SERVER_CSR, out_dir, CA_CRT,
           out_dir, CA_KEY, out_dir, SERVER_CRT, out_dir),
        # 完整链（叶在前 + CA 在后）——服务端 load_cert_chain 发送完整链
        "cat %s/%s %s/%s > %s/%s" % (out_dir, SERVER_CRT, out_dir, CA_CRT,
                                     out_dir, FULLCHAIN_CRT),
        # 私钥权限 + 清理 CSR/serial（留存亦可，删去减少敏感面）
        "chmod 600 %s/%s %s/%s %s/%s" % (out_dir, CA_KEY, out_dir, SERVER_KEY,
                                         out_dir, FULLCHAIN_CRT),
        "openssl x509 -in %s/%s -noout -fingerprint -sha256"
        % (out_dir, CA_CRT),
    ]
    return cmds


# ---------------------------------------------------------------- local 模式
def gen_local(out_dir, san_dns=DEFAULT_SAN_DNS, san_ip=DEFAULT_SAN_IP,
              ca_cn=CA_CN, ca_days=CA_DAYS, server_days=SERVER_DAYS):
    """cryptography 生成 CA + 服务器证书（开发/测试）。返回 CA 证书 DER。"""
    try:
        import cryptography
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError("local 模式需要 cryptography 库（pip install cryptography）；"
                           "生产路径请走 remote/openssl（deploy.py）")

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    def name(cn):
        return x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EyeTerm"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ])

    now = datetime.datetime.now(datetime.timezone.utc)

    # ---- 根 CA ----
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_cert = (x509.CertificateBuilder()
               .subject_name(name(ca_cn)).issuer_name(name(ca_cn))
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=ca_days))
               .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                              critical=True)
               .add_extension(x509.KeyUsage(
                   digital_signature=False, content_commitment=False,
                   key_encipherment=False, data_encipherment=False,
                   key_agreement=False, key_cert_sign=True, crl_sign=True,
                   encipher_only=None, decipher_only=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                   ca_key.public_key()), critical=False)
               .sign(ca_key, hashes.SHA256()))

    # ---- 服务器证书 ----
    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    san = [x509.DNSName(san_dns)]
    if san_ip:
        san.append(x509.IPAddress(ipaddress.ip_address(san_ip)))
    srv_cert = (x509.CertificateBuilder()
                .subject_name(name(san_dns)).issuer_name(ca_cert.subject)
                .public_key(srv_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=server_days))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                               critical=True)
                .add_extension(x509.KeyUsage(
                    digital_signature=True, content_commitment=False,
                    key_encipherment=True, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=None, decipher_only=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage(
                    [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName(san), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                    srv_key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))

    def _write(fn, data, private=False):
        path = os.path.join(out_dir, fn)
        with open(path, "wb") as fh:
            fh.write(data)
        if os.name != "nt" and private:  # Windows 下 chmod 语义尽力而为
            os.chmod(path, 0o600)
        return path

    _write(CA_KEY, ca_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()), private=True)
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    ca_crt_path = _write(CA_CRT, ca_pem)
    _write(SERVER_KEY, srv_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()), private=True)
    srv_pem = srv_cert.public_bytes(serialization.Encoding.PEM)
    _write(SERVER_CRT, srv_pem)
    # 完整链（叶在前 + CA 在后）——与服务端发送口径一致
    fullchain_path = _write(FULLCHAIN_CRT, srv_pem + ca_pem)

    ca_der = ca_cert.public_bytes(serialization.Encoding.DER)
    import hashlib
    fp = hashlib.sha256(ca_der).hexdigest()
    print("local certs written: %s" % out_dir)
    print("  ca.crt     : %s" % ca_crt_path)
    print("  server.crt : SAN=DNS:%s,IP:%s (days=%d)" % (san_dns, san_ip, server_days))
    print("  fullchain  : %s" % fullchain_path)
    print("  CA SHA256  : %s" % fp)
    return ca_der


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "certs"))
    ap.add_argument("--san-dns", default=DEFAULT_SAN_DNS)
    ap.add_argument("--san-ip", default=DEFAULT_SAN_IP)
    args = ap.parse_args()
    gen_local(args.out, args.san_dns, args.san_ip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
