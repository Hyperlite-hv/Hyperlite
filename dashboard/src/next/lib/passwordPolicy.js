// Mirror of app/core/password_policy.py, for feedback while typing. The server stays the authority: if it
// still refuses a password, its message is shown.
export const MIN_LENGTH = 12;
export const MAX_BYTES = 72;
const MIN_DISTINCT = 5;

const SEQUENCES = ["0123456789", "abcdefghijklmnopqrstuvwxyz", "azertyuiopqsdfghjklmwxcvbn", "qwertyuiopasdfghjklzxcvbnm", "qwertzuiopasdfghjklyxcvbnm"];
const COMMON = new Set([
  "password", "motdepasse", "azerty", "azertyuiop", "qwerty", "qwertyuiop", "admin", "administrator",
  "administrateur", "hyperlite", "hypervisor", "hyperviseur", "root", "toor", "welcome", "bienvenue",
  "letmein", "changeme", "secret", "default", "iloveyou", "jetaime", "soleil", "doudou", "loulou",
  "marseille", "proxmox", "vmware", "libvirt", "ubuntu", "debian", "monkey", "dragon", "football",
  "sunshine", "princess", "master", "superman", "trustno", "login", "user", "guest",
]);
const LOOKALIKE = { "@": "a", 4: "a", 0: "o", 1: "i", "!": "i", 3: "e", $: "s", 5: "s", 7: "t" };

const bytes = (s) => new TextEncoder().encode(s).length;
const isSequence = (low) => SEQUENCES.some((seq) => {
  const wrapped = seq.repeat(3);
  return wrapped.includes(low) || [...wrapped].reverse().join("").includes(low);
});
// Trims everything but letters from both ends with a plain scan, like the server (a trailing-run regular
// expression backtracks quadratically, and JavaScript's \W would also strip accented letters).
const isLetter = (c) => /\p{L}/u.test(c);
const baseWord = (low) => {
  let start = 0;
  let end = low.length;
  while (start < end && !isLetter(low[start])) start += 1;
  while (end > start && !isLetter(low[end - 1])) end -= 1;
  return low.slice(start, end).replace(/[@4013!$57]/g, (c) => LOOKALIKE[c]);
};

// The rules shown as a checklist under the field; `ok` is false while a rule is not met.
export function passwordChecks(password, username) {
  const low = password.toLowerCase();
  const name = (username || "").toLowerCase();
  return [
    { id: "length", ok: password.length >= MIN_LENGTH && bytes(password) <= MAX_BYTES, tooLong: bytes(password) > MAX_BYTES },
    { id: "name", ok: password.length > 0 && !(name.length >= 3 && low.includes(name)) },
    { id: "variety", ok: password.length > 0 && new Set(low).size >= MIN_DISTINCT && !isSequence(low) },
    { id: "common", ok: password.length > 0 && !COMMON.has(low) && !COMMON.has(baseWord(low)) },
  ];
}

export const passwordAccepted = (password, username) => passwordChecks(password, username).every((c) => c.ok);

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
// Largest multiple of the alphabet size under 2^32: values at or above it are drawn again, so every
// character is exactly as likely as the others (a plain modulo would slightly favour the first ones).
const UNBIASED_LIMIT = Math.floor(2 ** 32 / ALPHABET.length) * ALPHABET.length;

function randomChar() {
  const one = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(one);
    if (one[0] < UNBIASED_LIMIT) return ALPHABET[one[0] % ALPHABET.length];
  }
}

// A random password that meets the rules: 4 groups of 5 characters from an alphabet without look-alike
// characters (no 0/O, 1/l/I), drawn uniformly with the browser's cryptographic generator (~115 bits).
export function generatePassword(username) {
  for (;;) {
    const chars = Array.from({ length: 20 }, randomChar).join("");
    const pw = chars.match(/.{5}/g).join("-");
    if (passwordAccepted(pw, username)) return pw;
  }
}
