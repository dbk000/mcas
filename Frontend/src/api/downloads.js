function sanitizeFilename(filename) {
  if (!filename || typeof filename !== 'string') {
    return ''
  }

  return filename.trim().replace(/^["']|["']$/g, '')
}

function getDownloadFilename(contentDisposition, fallbackFilename = 'download') {
  if (!contentDisposition) {
    return fallbackFilename
  }

  const utf8Match = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      const decoded = decodeURIComponent(utf8Match[1])
      return sanitizeFilename(decoded) || fallbackFilename
    } catch {
      return sanitizeFilename(utf8Match[1]) || fallbackFilename
    }
  }

  const filenameMatch = contentDisposition.match(/filename\s*=\s*([^;]+)/i)
  if (filenameMatch?.[1]) {
    return sanitizeFilename(filenameMatch[1]) || fallbackFilename
  }

  return fallbackFilename
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')

  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()

  URL.revokeObjectURL(url)
}

export async function downloadResponseBlob(response, fallbackFilename) {
  const blob = await response.blob()
  const filename = getDownloadFilename(
    response.headers.get('Content-Disposition'),
    fallbackFilename
  )

  triggerDownload(blob, filename)
  return filename
}
