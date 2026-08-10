/**
 * Verify a backup archive passphrase client-side using WebCrypto.
 * Reads only the first 8KB of the file to extract wrapped_key.enc.
 * Returns true if passphrase is correct, false if wrong, throws if not a valid archive.
 */
export async function verifyBackupPassphrase(file: File, passphrase: string): Promise<boolean> {
  // Read the entire file — backup archives are small (typically < 100KB)
  // and this is a local File object, so there is no network cost.
  const buffer = await file.arrayBuffer();

  // 2. Decompress the gzip stream to get raw tar bytes
  const ds = new DecompressionStream('gzip');
  const writer = ds.writable.getWriter();
  const reader = ds.readable.getReader();

  writer.write(new Uint8Array(buffer));
  writer.close();

  // Collect decompressed bytes
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
  } catch {
    // Gzip may error at boundary since we only read 8KB — that's fine
    // we just need the first few entries
  }

  const tarBytes = new Uint8Array(chunks.reduce((acc, c) => acc + c.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    tarBytes.set(chunk, offset);
    offset += chunk.length;
  }

  // 3. Parse tar headers to find wrapped_key.enc
  // Tar format: 512-byte header + data padded to 512-byte blocks
  const wrappedKeyJson = extractTarFile(tarBytes, 'wrapped_key.enc');
  if (!wrappedKeyJson) {
    throw new Error('Not a valid backup archive (wrapped_key.enc not found)');
  }

  // 4. Parse the wrapped key
  const wrapped = JSON.parse(new TextDecoder().decode(wrappedKeyJson)) as {
    salt: string;
    nonce: string;
    ciphertext: string;
  };

  // 5. Verify using WebCrypto
  return verifyWithWebCrypto(passphrase, wrapped);
}

function extractTarFile(tarBytes: Uint8Array, filename: string): Uint8Array | null {
  let pos = 0;
  const decoder = new TextDecoder();

  while (pos + 512 <= tarBytes.length) {
    const header = tarBytes.slice(pos, pos + 512);

    // Check for end-of-archive (two zero blocks)
    if (header.every((b) => b === 0)) break;

    // File name is first 100 bytes, null-terminated
    const name = decoder.decode(header.slice(0, 100)).replace(/\0/g, '').trim();

    // File size is bytes 124-135, octal ASCII
    const sizeOctal = decoder.decode(header.slice(124, 136)).replace(/\0/g, '').trim();
    const size = parseInt(sizeOctal, 8);

    if (isNaN(size)) break;

    const dataStart = pos + 512;
    const dataEnd = dataStart + size;

    // Match by filename (may include ./ prefix)
    if (name === filename || name === `./${filename}`) {
      return tarBytes.slice(dataStart, dataEnd);
    }

    // Advance to next entry (padded to 512-byte blocks)
    const blocks = Math.ceil(size / 512);
    pos += 512 + blocks * 512;
  }

  return null;
}

async function verifyWithWebCrypto(
  passphrase: string,
  wrapped: { salt: string; nonce: string; ciphertext: string }
): Promise<boolean> {
  try {
    const salt = base64ToBytes(wrapped.salt);
    const nonce = base64ToBytes(wrapped.nonce);
    const ciphertext = base64ToBytes(wrapped.ciphertext);

    // Import passphrase as raw key material for PBKDF2
    const keyMaterial = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(passphrase),
      'PBKDF2',
      false,
      ['deriveKey']
    );

    // Derive AES-256-GCM key using PBKDF2-HMAC-SHA256, 100k iterations
    const aesKey = await crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        hash: 'SHA-256',
        salt,
        iterations: 100_000,
      },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt']
    );

    // Attempt AES-GCM decryption — throws if passphrase is wrong
    await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: nonce },
      aesKey,
      ciphertext
    );

    return true;
  } catch {
    return false;
  }
}

function base64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(b64);
  const buffer = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
