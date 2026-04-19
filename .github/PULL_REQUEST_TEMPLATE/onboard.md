## New NTRIP registration

**The GitHub account that opens this PR will receive the credentials.**
Make sure your account has at least one ed25519 or RSA SSH key registered at https://github.com/settings/keys the bot encrypts the password to those keys.

### Checklist

- [ ] I have added my entry under `opa.registry.base_stations` or `opa.registry.rovers` in `ops/values.yaml`
- [ ] I have **not** included a `password_hash` field (the bot generates it)
- [ ] I have **not** included a `github_user` field (inferred from this PR's author)
- [ ] My GitHub account has an ed25519 or RSA SSH key registered

### Entry format

**Base station:**
```yaml
opa:
  registry:
    base_stations:
      MY_MOUNTPOINT:
        mountpoint: MY_MOUNTPOINT
        valid_from: "YYYY-MM-DD"
        valid_until: "YYYY-MM-DD"
```

**Rover:**
```yaml
opa:
  registry:
    rovers:
      my-username:
        mountpoints: ["MY_MOUNTPOINT"]   # or ["*"] for all mountpoints
        valid_from: "YYYY-MM-DD"
        valid_until: "YYYY-MM-DD"
```

### What happens next

1. The bot runs automatically. It validates that only your entry was added to
   ops/values.yaml, generates a random password, and posts it encrypted in a
   comment on this PR. Only you can decrypt it.
2. Decrypt the password with:
   ```sh
   # Copy the age-encrypted block from the PR comment, then:
   echo '<paste block here>' | age -d -i ~/.ssh/your-private-key
   ```
   If you have multiple SSH keys, try every registered key until one works.
3. The bot commits your entry directly to main and closes this PR. You do not
   need to wait for a maintainer and should not try to merge it yourself.
4. Store the decrypted password securely. It will not be shown again. To
   rotate, open a new PR using the **Credential rotation** template.