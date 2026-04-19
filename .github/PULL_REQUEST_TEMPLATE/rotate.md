## Credential rotation

**This PR must be opened by the GitHub account that originally registered the entry.**

The bot verifies your identity against the key fingerprints recorded at the time
of your original onboarding.

### Checklist

 1. I am the original owner of the entry I want to rotate
 2. I have removed **only** the `password_hash` field from my entry in `ops/values.yaml` — nothing else
 3. My GitHub account still has the same ed25519 or RSA SSH key registered (or I have added a new one at https://github.com/settings/keys before opening this PR)

### What happens next

1. The bot runs automatically. It verifies that this PR is opened by the
   registered owner of the entry, then generates a new password and posts it
   encrypted in a comment. The comment includes a ready-to-run decrypt command
   with the block already inlined and the fingerprints of the keys it was
   encrypted to. Replace `~/.ssh/your-private-key` with the matching key and
   run it.
2. The bot commits the updated entry directly to main and closes this PR. You
   do not need to wait for a maintainer and should not try to merge it yourself.
3. Your old password stops working on the next deployment. Use the new one from
   that point on.

### Note on key changes

If your SSH key has changed since onboarding, add the new key to your GitHub
account **before** opening this PR. The bot encrypts to all ed25519/RSA keys
currently on your account, so your new key will work for decryption.
