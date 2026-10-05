# rootmab: /etc/nginx/sites-available/fxhash.gakoy.com
# Git forge of the fxhash projects, crawled by Software Heritage (Add Forge Now,
# type grokmirror, URL https://fxhash.gakoy.com/). Repos live in /srv/fxhash-git.
#
# Installed HTTP-only; then
#   sudo certbot --nginx --redirect --cert-name fxhash.gakoy.com -d fxhash.gakoy.com
# adds the "managed by Certbot" 443 lines and the HTTP->HTTPS redirect block.

server {
    listen 80;
    server_name fxhash.gakoy.com;
    server_tokens off;

    root /srv/fxhash-git;

    include /etc/nginx/snippets/swh-git-locations.conf;

    location = / {
        default_type text/plain;
        return 200 "Source code of fxhash projects, one git repository per project, archived by Software Heritage.\nRepository list: https://fxhash.gakoy.com/manifest.js.gz\n";
    }

    location / {
        return 404;
    }
}
