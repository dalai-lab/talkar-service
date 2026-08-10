/**
 * Talkar Admin API client.
 * Reads `talkar_admin_token` cookie and attaches it as Authorization header.
 */

const TALKAR_API = process.env.NEXT_PUBLIC_TALKAR_API_URL || "http://localhost:8001";

function getToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/talkar_admin_token=([^;]+)/);
  return match ? match[1] : "";
}

export async function adminFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  return fetch(`${TALKAR_API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
}

export { TALKAR_API };
