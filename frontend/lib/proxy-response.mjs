export async function readProxyBody(response) {
  if (response.status === 204 || response.status === 205 || response.status === 304) {
    return null;
  }
  return response.text();
}
