import { useRef, useState } from 'react';
import { Globe2, Plus, RefreshCcw, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  useCreateKnowledgeEntry,
  useImportWebsiteKnowledge,
  useUploadDocumentKnowledge,
} from '@/features/knowledge/api';
import { UPLOAD_ACCEPT, UPLOAD_HELPER_TEXT } from '@/features/knowledge/constants';
import { isValidKnowledgeUrl } from '@/features/knowledge/utils';

export const MANUAL_KNOWLEDGE_TITLE_ID = 'knowledge-title-input';
export const WEBSITE_IMPORT_URL_ID = 'knowledge-website-url-input';
export const DOCUMENT_UPLOAD_TITLE_ID = 'knowledge-upload-title-input';

export function KnowledgeCreatePanel() {
  const createEntry = useCreateKnowledgeEntry();
  const importWebsite = useImportWebsiteKnowledge();
  const uploadDocument = useUploadDocumentKnowledge();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [source, setSource] = useState<'manual' | 'document'>('manual');
  const [url, setUrl] = useState('');
  const [websiteTitle, setWebsiteTitle] = useState('');
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [manualSubmitted, setManualSubmitted] = useState(false);
  const [websiteSubmitted, setWebsiteSubmitted] = useState(false);
  const [uploadSubmitted, setUploadSubmitted] = useState(false);

  const titleError = (manualSubmitted || title.trim().length > 0) && title.trim().length < 2 ? 'Enter at least 2 characters.' : '';
  const bodyError = (manualSubmitted || body.trim().length > 0) && body.trim().length < 10 ? 'Enter at least 10 characters.' : '';
  const urlError = (websiteSubmitted || url.trim().length > 0) && !isValidKnowledgeUrl(url) ? 'Enter a URL starting with http:// or https://.' : '';
  const uploadTitleError = (uploadSubmitted || uploadTitle.trim().length > 0) && uploadTitle.trim().length < 2 ? 'Enter at least 2 characters.' : '';
  const uploadFileError = uploadSubmitted && !uploadFile ? 'Choose a TXT, Markdown, or readable PDF file.' : '';

  const canCreate = title.trim().length >= 2 && body.trim().length >= 10 && !createEntry.isPending;
  const canImport = isValidKnowledgeUrl(url) && !importWebsite.isPending;
  const canUpload = uploadTitle.trim().length >= 2 && uploadFile !== null && !uploadDocument.isPending;

  const resetKnowledgeForm = () => {
    setTitle('');
    setBody('');
    setManualSubmitted(false);
  };

  const handleManualChange = (field: 'title' | 'body', value: string) => {
    setNotice(null);
    if (field === 'title') {
      setTitle(value);
    } else {
      setBody(value);
    }
  };

  return (
    <section className="space-y-3" aria-label="Add knowledge">
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded-md border bg-card p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-[15px] font-semibold text-foreground">Create manual answer</h2>
              <p className="mt-1 text-[12px] text-muted-foreground">Add approved information AWA can use in replies.</p>
            </div>
            <label className="flex flex-col gap-1 text-[12px] font-medium text-muted-foreground">
              Source type
              <select
                value={source}
                onChange={(event) => setSource(event.target.value as 'manual' | 'document')}
                className="h-9 rounded-md border bg-background px-2 text-[13px] text-foreground"
                aria-label="Knowledge source type"
              >
                <option value="manual">Manual</option>
                <option value="document">Document text</option>
              </select>
            </label>
          </div>
          <div className="mt-4 space-y-3">
            <div>
              <label htmlFor={MANUAL_KNOWLEDGE_TITLE_ID} className="text-[12px] font-medium text-foreground">
                Title or customer question
              </label>
              <Input
                id={MANUAL_KNOWLEDGE_TITLE_ID}
                value={title}
                onChange={(event) => handleManualChange('title', event.target.value)}
                placeholder="What should customers ask?"
                className="mt-1 h-9 text-[13px]"
                aria-invalid={Boolean(titleError)}
                aria-describedby="knowledge-title-helper knowledge-title-error"
              />
              <p id="knowledge-title-helper" className="mt-1 text-[11px] text-muted-foreground">
                Use a clear question, policy name, or FAQ title.
              </p>
              {titleError && <p id="knowledge-title-error" className="mt-1 text-[12px] text-destructive">{titleError}</p>}
            </div>
            <div>
              <label htmlFor="knowledge-body-input" className="text-[12px] font-medium text-foreground">
                Approved answer
              </label>
              <Textarea
                id="knowledge-body-input"
                value={body}
                onChange={(event) => handleManualChange('body', event.target.value)}
                placeholder="What should AWA say?"
                rows={5}
                className="mt-1 resize-none text-[13px]"
                aria-invalid={Boolean(bodyError)}
                aria-describedby="knowledge-body-helper knowledge-body-error"
              />
              <p id="knowledge-body-helper" className="mt-1 text-[11px] text-muted-foreground">
                Keep it safe for customers to see.
              </p>
              {bodyError && <p id="knowledge-body-error" className="mt-1 text-[12px] text-destructive">{bodyError}</p>}
            </div>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="min-h-5 text-[12px] text-muted-foreground" aria-live="polite">
              {notice || (createEntry.isError ? 'Could not add this knowledge.' : '')}
            </p>
            <Button
              size="sm"
              disabled={!canCreate}
              onClick={() => {
                setManualSubmitted(true);
                if (!canCreate) return;
                createEntry.mutate(
                  { documentTitle: title.trim(), chunkText: body.trim(), source },
                  {
                    onSuccess: () => {
                      setNotice('Added');
                      resetKnowledgeForm();
                    },
                  }
                );
              }}
            >
              {createEntry.isPending ? <RefreshCcw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Plus className="h-3.5 w-3.5" aria-hidden="true" />}
              Add
            </Button>
          </div>
        </div>

        <div className="rounded-md border bg-card p-4">
          <div>
            <h2 className="text-[15px] font-semibold text-foreground">Import website</h2>
            <p className="mt-1 text-[12px] text-muted-foreground">Import readable public page text for AWA to search when relevant.</p>
          </div>
          <div className="mt-4 space-y-3">
            <div>
              <label htmlFor={WEBSITE_IMPORT_URL_ID} className="text-[12px] font-medium text-foreground">Website URL</label>
              <Input
                id={WEBSITE_IMPORT_URL_ID}
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value);
                  setWebsiteSubmitted(false);
                }}
                placeholder="https://example.com/services"
                className="mt-1 h-9 text-[13px]"
                aria-invalid={Boolean(urlError)}
                aria-describedby="knowledge-url-helper knowledge-url-error"
              />
              <p id="knowledge-url-helper" className="mt-1 text-[11px] text-muted-foreground">
                AWA imports readable public text from this page. Progress is shown only while the request is running.
              </p>
              {urlError && <p id="knowledge-url-error" className="mt-1 text-[12px] text-destructive">{urlError}</p>}
            </div>
            <div>
              <label htmlFor="knowledge-website-title-input" className="text-[12px] font-medium text-foreground">Optional title</label>
              <Input
                id="knowledge-website-title-input"
                value={websiteTitle}
                onChange={(event) => setWebsiteTitle(event.target.value)}
                placeholder="Services page"
                className="mt-1 h-9 text-[13px]"
              />
            </div>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:items-end">
            <Button
              size="sm"
              variant="outline"
              disabled={!canImport}
              onClick={() => {
                setWebsiteSubmitted(true);
                if (!canImport) return;
                importWebsite.mutate(
                  { url: url.trim(), title: websiteTitle.trim() || undefined },
                  {
                    onSuccess: () => {
                      setUrl('');
                      setWebsiteTitle('');
                      setWebsiteSubmitted(false);
                    },
                  }
                );
              }}
            >
              {importWebsite.isPending ? <RefreshCcw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Globe2 className="h-3.5 w-3.5" aria-hidden="true" />}
              Import
            </Button>
            <p className="min-h-5 text-[12px] text-muted-foreground" aria-live="polite">
              {importWebsite.isPending ? 'Importing website text...' : importWebsite.isError ? 'Could not read this website.' : ''}
            </p>
          </div>
        </div>

        <div className="rounded-md border bg-card p-4">
          <div>
            <h2 className="text-[15px] font-semibold text-foreground">Upload document</h2>
            <p className="mt-1 text-[12px] text-muted-foreground">Upload document content AWA can use as reference material.</p>
          </div>
          <div className="mt-4 space-y-3">
            <div>
              <label htmlFor={DOCUMENT_UPLOAD_TITLE_ID} className="text-[12px] font-medium text-foreground">Document title</label>
              <Input
                id={DOCUMENT_UPLOAD_TITLE_ID}
                value={uploadTitle}
                onChange={(event) => {
                  setUploadTitle(event.target.value);
                  setUploadSubmitted(false);
                }}
                placeholder="Clinic policy"
                className="mt-1 h-9 text-[13px]"
                aria-invalid={Boolean(uploadTitleError)}
                aria-describedby="knowledge-upload-title-helper knowledge-upload-title-error"
              />
              <p id="knowledge-upload-title-helper" className="mt-1 text-[11px] text-muted-foreground">{UPLOAD_HELPER_TEXT}</p>
              {uploadTitleError && <p id="knowledge-upload-title-error" className="mt-1 text-[12px] text-destructive">{uploadTitleError}</p>}
            </div>
            <div>
              <label htmlFor="knowledge-upload-file-input" className="text-[12px] font-medium text-foreground">Document file</label>
              <Input
                ref={fileInputRef}
                id="knowledge-upload-file-input"
                type="file"
                accept={UPLOAD_ACCEPT}
                onChange={(event) => {
                  setUploadFile(event.target.files?.[0] ?? null);
                  setUploadSubmitted(false);
                }}
                className="mt-1 h-9 text-[13px]"
                aria-invalid={Boolean(uploadFileError)}
                aria-describedby="knowledge-upload-file-helper knowledge-upload-file-error"
              />
              <p id="knowledge-upload-file-helper" className="mt-1 text-[11px] text-muted-foreground">
                {uploadFile ? `Selected: ${uploadFile.name}` : 'Choose a supported knowledge document.'}
              </p>
              {uploadFileError && <p id="knowledge-upload-file-error" className="mt-1 text-[12px] text-destructive">{uploadFileError}</p>}
            </div>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:items-end">
            <Button
              size="sm"
              variant="outline"
              disabled={!canUpload}
              onClick={() => {
                setUploadSubmitted(true);
                if (!canUpload || !uploadFile) return;
                uploadDocument.mutate(
                  { title: uploadTitle.trim(), file: uploadFile },
                  {
                    onSuccess: () => {
                      setUploadTitle('');
                      setUploadFile(null);
                      setUploadSubmitted(false);
                      if (fileInputRef.current) fileInputRef.current.value = '';
                    },
                  }
                );
              }}
            >
              {uploadDocument.isPending ? <RefreshCcw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Upload className="h-3.5 w-3.5" aria-hidden="true" />}
              Upload
            </Button>
            <p className="min-h-5 text-[12px] text-muted-foreground" aria-live="polite">
              {uploadDocument.isPending ? 'Uploading document...' : uploadDocument.isError ? 'Could not upload this document.' : ''}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
