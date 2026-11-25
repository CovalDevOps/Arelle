# Pinned intermediate CA certificates

Used only by `service/warm_cache.py` when it fetches taxonomies online at build time. They are appended to
certifi's CA bundle; TLS verification stays on and every chain must still end at a root in that bundle.
They are not copied into the sandbox image (Arelle runs offline there).

| File | Why | Issuer (root) | SHA-256 fingerprint | Expires |
|---|---|---|---|---|
| `certsign-web-ca-g2.pem` | `www.nltaxonomie.nl` (Dutch SBR taxonomies) sends only its leaf certificate, without this intermediate. Fetched 23 November 2026 from the leaf's AIA URL `http://pkipro.certsign.ro/certsign-webcag2.crt`. | certSIGN ROOT CA G2 | `0E:49:BE:DB:D0:93:41:D6:46:EB:65:CE:E9:F2:F1:EE:6C:8F:88:BE:AA:90:69:79:A4:9D:16:16:FC:9C:03:EA` | 21 January 2033 |

Remove an entry once the server sends its full chain:

```
echo | openssl s_client -connect www.nltaxonomie.nl:443 -servername www.nltaxonomie.nl 2>/dev/null | grep "Verify return"
```
