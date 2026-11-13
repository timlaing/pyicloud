# Custom-password Notes envelope

A disposable private note opened on iCloud.com was returned as a
`PasswordProtectedNote` by a regular record lookup. The request shape had no
password field. The observed outer CloudKit iteration count was zero; the
outer wrapped-key field was 16 bytes. These fields are not sufficient to
identify the body cipher.

`TextDataEncrypted` held a binary `NSKeyedArchiver` property list rooted in
`ICCryptoEncryptionObject`. Its metadata property list declared cipher
version 2. The unauthenticated metadata property list contained a 32-byte
passphrase salt and 20,000 iterations. Separate archive objects held a
40-byte wrapped encryption key and the encrypted body. The parser accepts
this shape without instantiating archived classes and does not return the
password hint.

Apple describes PBKDF2 with SHA-256 and AES-GCM for secure Notes in
[Apple Platform Security](https://support.apple.com/guide/security/secure-features-in-the-notes-app-sec1782bcab1/web).
The captured browser bundle identifies archive version 2 as its Neo format.
It derives a 32-byte wrapping key using PBKDF2-SHA256 over the UTF-8 password
and the inner salt and iteration count. AES Key Wrap unwraps a 32-byte content
key. The encrypted body stores ciphertext followed by a 32-byte nonce and
a 16-byte tag. AES-GCM authenticates the exact inner metadata bytes as
additional data, not a re-encoded property list.

The internal helper returns authenticated body bytes only. It does not try
alternative passwords or fall back to the outer fields. A read-only check
against a disposable live note confirmed that the decrypted bytes decode as
a Notes document. The public read path bounds decompression and does not
expose locked attachments or permit locked writes.
