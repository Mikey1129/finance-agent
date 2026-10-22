import { marked } from 'marked'
import DOMPurify from 'dompurify'

export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
