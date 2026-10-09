# rootmab: /etc/nginx/sites-available/fxhash.gakoy.com
# Art gateway: every fxhash artwork whose code is a repo of /srv/fxhash-git, served from
# git by scripts/art_gateway.py (systemd: fxhash-art-gateway, 127.0.0.1:8095).
#   https://fxhash.gakoy.com/ipfs/<cid>/?fxhash=...  -> 302 -> https://<label>.fxhash.gakoy.com/...
# DNS: OVH A records fxhash + *.fxhash (gakoy.com zone). TLS: one Let's Encrypt cert for
# fxhash.gakoy.com + *.fxhash.gakoy.com, DNS-01 through /usr/local/sbin/certbot-ovh-dns01:
#   sudo certbot certonly --manual --preferred-challenges dns --cert-name fxhash.gakoy.com \
#       --manual-auth-hook "/usr/local/sbin/certbot-ovh-dns01 auth" \
#       --manual-cleanup-hook "/usr/local/sbin/certbot-ovh-dns01 cleanup" \
#       -d fxhash.gakoy.com -d '*.fxhash.gakoy.com'

server {
    listen 80;
    server_name fxhash.gakoy.com *.fxhash.gakoy.com;
    server_tokens off;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;  # like the other vhosts (nginx 1.24: no "http2 on")
    server_name fxhash.gakoy.com *.fxhash.gakoy.com;
    server_tokens off;

    ssl_certificate /etc/letsencrypt/live/fxhash.gakoy.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fxhash.gakoy.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        limit_except GET HEAD { deny all; }
        proxy_pass http://127.0.0.1:8095;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_read_timeout 60s;
    }
}
