## Credential rotation

**This PR must be opened by the GitHub account that originally registered the entry.**
The bot verifies your identity against the key fingerprints recorded at the time
of your original onboarding.

### Checklist

- [ ] I am the original owner of the entry I want to rotate
- [ ] I have removed **only** the `password_hash` field from my entry in `ops/values.yaml` — nothing else
- [ ] My GitHub account still has the same ed25519 or RSA SSH key registered (or I have added a new one at https://github.com/settings/keys before opening this PR)

### What happens next

1. A maintainer reviews and approves the **Production** environment gate.
2. The bot verifies that this PR is opened by the registered owner of the entry.
3. A new password is generated and posted encrypted in a comment — only you can decrypt it:
   ```sh
   # Copy the age-encrypted block from the PR comment, then:
   echo '<paste block here>' | age -d -i ~/.ssh/your-private-key
   ```
4. The maintainer merges the PR. Your old password stops working immediately
   on deploy; use the new one from that point on.

### Note on key changes

If your SSH key has changed since onboarding, add the new key to your GitHub
account **before** opening this PR. The bot encrypts to all ed25519/RSA keys
currently on your account, so your new key will work for decryption.
