// Browser side of security keys (WebAuthn). The server sends py_webauthn's JSON options (binary fields in
// base64url); the browser API wants ArrayBuffers, and its answer goes back as JSON with base64url again.

const toBytes = (b64url) => {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64url.length + 3) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer;
};
const toB64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const descriptors = (list) => (list || []).map((c) => ({ ...c, id: toBytes(c.id) }));

// Browsers refuse WebAuthn on an IP address (even 127.0.0.1, which is otherwise a secure context) and outside HTTPS.
const onIpAddress = (host) => /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":") || host.startsWith("[");

export function webauthnSupported() {
  return typeof window !== "undefined" && !!window.PublicKeyCredential && !!navigator.credentials && window.isSecureContext
    && !onIpAddress(window.location.hostname);
}

export async function createCredential(options) {
  const cred = await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: toBytes(options.challenge),
      user: { ...options.user, id: toBytes(options.user.id) },
      excludeCredentials: descriptors(options.excludeCredentials),
    },
  });
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(cred.response.clientDataJSON),
      attestationObject: toB64url(cred.response.attestationObject),
      transports: cred.response.getTransports ? cred.response.getTransports() : undefined,
    },
  };
}

export async function getAssertion(options) {
  const cred = await navigator.credentials.get({
    publicKey: { ...options, challenge: toBytes(options.challenge), allowCredentials: descriptors(options.allowCredentials) },
  });
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(cred.response.clientDataJSON),
      authenticatorData: toB64url(cred.response.authenticatorData),
      signature: toB64url(cred.response.signature),
      userHandle: cred.response.userHandle ? toB64url(cred.response.userHandle) : undefined,
    },
  };
}
