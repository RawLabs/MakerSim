async function responseJson(response) {
  if (!response.ok) {
    let message = `The local service returned ${response.status}.`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : 'Check the print settings, selected areas, and force value.';
    } catch { /* Preserve a useful message for non-JSON server errors. */ }
    throw new Error(message);
  }
  return response.json();
}
export const getMaterials = () => fetch('/api/materials').then(responseJson);
export async function uploadModel(file, unit) {
  const body = new FormData();
  body.append('file',file); body.append('unit',unit);
  return responseJson(await fetch('/api/models',{method:'POST',body}));
}
export async function simulate(payload) {
  return responseJson(await fetch('/api/simulate',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
  }));
}
