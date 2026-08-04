import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, Pause, Play, QrCode, RotateCcw, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTenantStore } from '@/stores/tenant.store';
import { getApiErrorMessage } from '@/lib/api-errors';
import { PageHeader } from '@/components/shared/PageHeader';
import { CopyButton } from '@/components/shared/CopyButton';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { useAwaChatLinks, useCreateAwaChatLink, useUpdateAwaChatLink, useRotateAwaChatLink, type AwaChatLink } from '@/features/awa-chat/api';

const isValidHex = (hex: string) => /^#[0-9A-Fa-f]{6}$/.test(hex);

export function AwaChatPage() {
  const tenantName = useTenantStore((state) => state.activeTenantName);
  const { data: links, isLoading, isError } = useAwaChatLinks();
  const createLink = useCreateAwaChatLink();
  const updateLink = useUpdateAwaChatLink();
  const rotateLink = useRotateAwaChatLink();
  const [rotateConfirmOpen, setRotateConfirmOpen] = useState(false);
  const [displayName, setDisplayName] = useState(tenantName || 'AWA Chat');
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [pendingStatusChange, setPendingStatusChange] = useState<{ link: AwaChatLink; status: AwaChatLink['status'] } | null>(null);
  const activeLink = useMemo(() => links?.[0], [links]);
  const activePublicUrl = useMemo(() => normalizeLocalPublicChatUrl(activeLink?.public_url), [activeLink?.public_url]);
  const publicStatus = activeLink?.public_status || activeLink?.status;
  const linkIsUsable = activeLink?.is_publicly_accessible ?? activeLink?.status === 'active';
  const linkNeedsRotation = publicStatus === 'needs_rotation';

  // Widget customizer states
  const [primaryColor, setPrimaryColor] = useState('#4f46e5');
  const [bgColor, setBgColor] = useState('#18181b');
  const [textColor, setTextColor] = useState('#ffffff');
  const [primaryError, setPrimaryError] = useState('');
  const [bgError, setBgError] = useState('');
  const [textError, setTextError] = useState('');

  const handlePrimaryChange = (val: string) => {
    setPrimaryColor(val);
    if (!isValidHex(val)) {
      setPrimaryError('Use valid hex like #4f46e5');
    } else {
      setPrimaryError('');
    }
  };

  const handleBgChange = (val: string) => {
    setBgColor(val);
    if (!isValidHex(val)) {
      setBgError('Use valid hex like #18181b');
    } else {
      setBgError('');
    }
  };

  const handleTextChange = (val: string) => {
    setTextColor(val);
    if (!isValidHex(val)) {
      setTextError('Use valid hex like #ffffff');
    } else {
      setTextError('');
    }
  };

  // const rotateQrUnavailableReason = linkNeedsRotation
  //   ? 'This link needs a replacement. Direct QR rotation is not available here, so create a replacement link and share the newest QR.'
  //   : 'Direct QR rotation is not available here yet. Create a replacement link when the current QR needs to change.';
  const statusActionUnavailableReason = linkNeedsRotation
    ? 'Create a replacement link before changing availability. The current public link cannot validate safely.'
    : null;

  useEffect(() => {
    if (!actionNotice) {
      return;
    }
    const timer = window.setTimeout(() => setActionNotice(null), 3500);
    return () => window.clearTimeout(timer);
  }, [actionNotice]);

  const handleCreate = async () => {
    try {
      await createLink.mutateAsync(displayName);
      toast.success('AWA Chat link created');
      setActionNotice('Public chat link created and ready to share.');
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not create AWA Chat link'));
    }
  };

  const handleCreateReplacement = async (link: AwaChatLink) => {
    try {
      await createLink.mutateAsync(link.display_name);
      toast.success('Replacement AWA Chat link created');
      setActionNotice('Replacement public chat link created. Use the newest QR/link before sharing.');
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not create replacement link'));
    }
  };

  const handleOpen = (value: string) => {
    window.open(value, '_blank');
    setActionNotice('Public chat opened in a new tab.');
  };

  const handleStatus = async (link: AwaChatLink, status: AwaChatLink['status']) => {
    try {
      const updatedLink = await updateLink.mutateAsync({ id: link.id, status });
      if (status === 'active' && updatedLink.is_publicly_accessible === false) {
        toast.success('Link status updated, but replacement is still required');
        setActionNotice(updatedLink.status_reason || 'This link still needs a replacement before customers can use it.');
      } else {
        toast.success(status === 'active' ? 'AWA Chat resumed' : 'AWA Chat paused');
      }
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Could not update link'));
    }
  };

  if (isLoading) {
    return <div className="h-44 rounded-md border bg-card animate-pulse" />;
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="AWA Chat"
        description="Share a QR or link so customers can chat and book from any browser."
        actions={
          activeLink && linkIsUsable ? (
            <CopyButton
              size="sm"
              className="h-9 text-[13px] sm:h-8"
              value={activePublicUrl}
              onClick={() => setActionNotice('Public chat link copied.')}
            >
              Copy public link
            </CopyButton>
          ) : undefined
        }
      />
      <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3 text-[13px] text-foreground">
        <div className="flex items-start gap-2.5">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
          <p>
            AWA Chat avoids WhatsApp setup and Meta message charges. AWA plan limits and AI credits still apply when AI replies are generated.
          </p>
        </div>
      </div>
      <div className="rounded-md border bg-card p-4 text-[13px]">
        <div className="flex items-start gap-2.5">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div>
            <p className="font-medium text-foreground">Pilot share checklist</p>
            <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
              Treat this as a public customer link. Test it yourself before sending it to friendly pilot users.
            </p>
          </div>
        </div>
        <div className="mt-3 grid gap-2 text-[12px] leading-5 text-muted-foreground sm:grid-cols-2">
          <p>This link can be opened by anyone who has the URL or QR code.</p>
          <p>AI replies use enabled Workflow Tools and approved Knowledge.</p>
          <p>CAPTCHA, phone OTP, and contact capture are controlled by Business Setup.</p>
          <p>In local or test mode, do not treat this as paid-production ready.</p>
        </div>
      </div>
      {isError && (
        <div className="rounded-md border border-destructive/20 bg-destructive/5 p-3 text-[13px] text-destructive">
          Failed to load AWA Chat links. Refresh the page and try again.
        </div>
      )}
      {actionNotice && (
        <div className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/10 p-3 text-[13px] text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{actionNotice}</p>
        </div>
      )}
      {activeLink && !linkIsUsable && (
        <div className="rounded-md border border-amber-500/25 bg-amber-500/10 p-3 text-[13px] text-amber-900 dark:text-amber-100">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex gap-2.5">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">
                  {linkNeedsRotation ? 'This AWA Chat link needs rotation' : 'This AWA Chat link is not available to customers'}
                </p>
                <p className="mt-1 text-[12px] leading-5 opacity-90">
                  {activeLink.status_reason || 'Customers cannot start new chats from this link right now.'}
                </p>
              </div>
            </div>
            {linkNeedsRotation && (
              <Button
                type="button"
                size="sm"
                className="h-8 shrink-0 bg-amber-100 text-zinc-950 hover:bg-amber-50"
                onClick={() => handleCreateReplacement(activeLink)}
                disabled={createLink.isPending}
              >
                {createLink.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="mr-1.5 h-3.5 w-3.5" />}
                Create Replacement
              </Button>
            )}
          </div>
        </div>
      )}
      {!activeLink ? (
        <Card>
          <CardContent className="space-y-4 p-5">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10">
                <QrCode className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-[14px] font-medium text-foreground">Create your first public chat link</p>
                <p className="text-[13px] text-muted-foreground">Use this on posters, counter displays, Instagram bio, website, or Google Business profile.</p>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Display name"
                className="h-9 text-[13px]"
              />
              <Button onClick={handleCreate} disabled={createLink.isPending} className="h-9 text-[13px]">
                {createLink.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <QrCode className="mr-1.5 h-3.5 w-3.5" />}
                Create AWA Chat link
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <Card>
              <CardContent className="space-y-4 p-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="text-[15px] font-semibold text-foreground">{activeLink.display_name}</p>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                        publicStatus === 'active'
                          ? 'bg-emerald-500/10 text-emerald-600'
                          : publicStatus === 'needs_rotation'
                            ? 'bg-red-500/10 text-red-600'
                          : 'bg-amber-500/10 text-amber-600'
                      }`}>
                        {publicStatus?.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <p className="mt-1 break-all text-[13px] text-muted-foreground">{activePublicUrl}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 px-3 text-[12px]"
                      onClick={() => handleOpen(activePublicUrl)}
                      disabled={!linkIsUsable}
                      aria-label="Test or open public chat link"
                      title={linkIsUsable ? 'Test or open public chat link' : activeLink.status_reason || 'Public chat link is not available'}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      Test/Open public link
                    </Button>
                    <CopyButton
                      variant="outline"
                      size="sm"
                      className="h-8 px-3 text-[12px]"
                      value={activePublicUrl}
                      disabled={!linkIsUsable}
                      aria-label="Copy public chat link"
                      title={linkIsUsable ? 'Copy public chat link' : activeLink.status_reason || 'Public chat link is not available'}
                      onClick={() => setActionNotice('Public chat link copied.')}
                    >
                      Copy
                    </CopyButton>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Metric label="Scans" value={activeLink.scan_count ?? 0} />
                  <Metric label="Channel" value="Browser" />
                  <Metric label="Setup" value="No Meta" />
                  <Metric label="Status" value={publicStatus?.replace(/_/g, ' ') || activeLink.status} />
                </div>
                <div className="space-y-2 border-t pt-4">
                  <div className="flex flex-wrap gap-2">
                    {activeLink.status === 'active' ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-[13px]"
                        onClick={() => setPendingStatusChange({ link: activeLink, status: 'paused' })}
                        disabled={updateLink.isPending || Boolean(statusActionUnavailableReason)}
                        aria-describedby={statusActionUnavailableReason ? 'awa-chat-status-action-disabled-reason' : undefined}
                        title={statusActionUnavailableReason || 'Pause public chat'}
                      >
                        <Pause className="mr-1.5 h-3.5 w-3.5" />
                        Pause
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-[13px]"
                        onClick={() => setPendingStatusChange({ link: activeLink, status: 'active' })}
                        disabled={updateLink.isPending || Boolean(statusActionUnavailableReason)}
                        aria-describedby={statusActionUnavailableReason ? 'awa-chat-status-action-disabled-reason' : undefined}
                        title={statusActionUnavailableReason || 'Resume public chat'}
                      >
                        <Play className="mr-1.5 h-3.5 w-3.5" />
                        Resume
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 text-[13px]"
                      onClick={() => setRotateConfirmOpen(true)}
                      disabled={rotateLink.isPending}
                      title="Rotate public link and QR code"
                    >
                      {rotateLink.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="mr-1.5 h-3.5 w-3.5" />}
                      Rotate QR
                    </Button>
                  </div>
                  {statusActionUnavailableReason && (
                    <p id="awa-chat-status-action-disabled-reason" className="max-w-2xl text-[12px] leading-5 text-muted-foreground">
                      {statusActionUnavailableReason}
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* AWA Chat Style Customizer Card */}
            <Card>
              <CardContent className="space-y-4 p-5">
                <div>
                  <h3 className="text-[14px] font-semibold text-foreground">AWA Chat Style Customizer</h3>
                  <p className="text-[12px] text-muted-foreground mt-0.5">Preview color changes here. Saving is unavailable until public chat theme persistence is wired to the API.</p>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <Label htmlFor="theme-primary" className="text-xs">Primary color</Label>
                      <div className="flex gap-2">
                        <Input
                          id="theme-primary"
                          className="h-8 text-xs font-mono"
                          value={primaryColor}
                          onChange={(e) => handlePrimaryChange(e.target.value)}
                        />
                        <input
                          type="color"
                          value={isValidHex(primaryColor) ? primaryColor : '#4f46e5'}
                          onChange={(e) => handlePrimaryChange(e.target.value)}
                          className="h-8 w-8 rounded border border-input cursor-pointer p-0 bg-transparent"
                          aria-label="Pick primary color"
                        />
                      </div>
                      {primaryError && <p className="text-[11px] text-destructive">{primaryError}</p>}
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="theme-bg" className="text-xs">Background color</Label>
                      <div className="flex gap-2">
                        <Input
                          id="theme-bg"
                          className="h-8 text-xs font-mono"
                          value={bgColor}
                          onChange={(e) => handleBgChange(e.target.value)}
                        />
                        <input
                          type="color"
                          value={isValidHex(bgColor) ? bgColor : '#18181b'}
                          onChange={(e) => handleBgChange(e.target.value)}
                          className="h-8 w-8 rounded border border-input cursor-pointer p-0 bg-transparent"
                          aria-label="Pick background color"
                        />
                      </div>
                      {bgError && <p className="text-[11px] text-destructive">{bgError}</p>}
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="theme-text" className="text-xs">Header Text color</Label>
                      <div className="flex gap-2">
                        <Input
                          id="theme-text"
                          className="h-8 text-xs font-mono"
                          value={textColor}
                          onChange={(e) => handleTextChange(e.target.value)}
                        />
                        <input
                          type="color"
                          value={isValidHex(textColor) ? textColor : '#ffffff'}
                          onChange={(e) => handleTextChange(e.target.value)}
                          className="h-8 w-8 rounded border border-input cursor-pointer p-0 bg-transparent"
                          aria-label="Pick header text color"
                        />
                      </div>
                      {textError && <p className="text-[11px] text-destructive">{textError}</p>}
                    </div>

                    <Button
                      type="button"
                      size="sm"
                      disabled
                      title="Theme preview is not persisted yet"
                      className="w-full h-8 text-[12px] mt-2"
                    >
                      Styling save unavailable
                    </Button>
                  </div>

                  {/* Mock Preview */}
                  <div className="border border-border rounded-lg p-3 bg-muted/20 flex flex-col items-center justify-center min-h-[220px]">
                    <p className="text-[11px] font-semibold text-muted-foreground mb-2">Live Preview</p>
                    <div
                      style={{ backgroundColor: isValidHex(bgColor) ? bgColor : '#18181b' }}
                      className="w-full max-w-[240px] rounded-lg shadow border border-border/60 overflow-hidden flex flex-col transition-all duration-300"
                    >
                      {/* Widget Header */}
                      <div
                        style={{
                          backgroundColor: isValidHex(primaryColor) ? primaryColor : '#4f46e5',
                          color: isValidHex(textColor) ? textColor : '#ffffff'
                        }}
                        className="p-2.5 flex items-center justify-between text-[11px] font-semibold"
                      >
                        <div className="flex items-center gap-1.5">
                          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                          <span className="truncate max-w-[120px]">{displayName}</span>
                        </div>
                        <span className="text-[9px] opacity-80">AI Mode</span>
                      </div>
                      {/* Widget Chat Area */}
                      <div className="p-2.5 space-y-2 min-h-[90px] bg-background/55 flex flex-col justify-end">
                        <div className="self-end bg-secondary/70 text-[10px] rounded px-2 py-1 max-w-[85%] text-foreground">
                          Hello!
                        </div>
                        <div
                          style={{
                            backgroundColor: isValidHex(primaryColor) ? primaryColor : '#4f46e5',
                            color: isValidHex(textColor) ? textColor : '#ffffff'
                          }}
                          className="self-start text-[10px] rounded px-2 py-1 max-w-[85%] transition-all duration-300"
                        >
                          Hi! How can I help you today?
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardContent className="space-y-3 p-5 text-center">
              {linkIsUsable ? (
                <>
                  <div className="mx-auto flex h-56 w-56 items-center justify-center rounded-md border bg-white p-3">
                    <img
                      src={`/api/v1/public-chat/links/${activeLink.slug}/qr`}
                      alt="AWA Chat QR code"
                      className="h-full w-full"
                    />
                  </div>
                  <p className="text-[13px] font-medium text-foreground">Customer QR</p>
                  <p className="text-[12px] text-muted-foreground">Print or place this on your counter. Customers scan it and start chatting instantly.</p>
                </>
              ) : (
                <>
                  <div className="mx-auto flex h-56 w-56 items-center justify-center rounded-md border border-amber-500/30 bg-amber-500/10 p-5 text-amber-700 dark:text-amber-100">
                    <div className="text-center">
                      <AlertTriangle className="mx-auto h-8 w-8" />
                      <p className="mt-3 text-[13px] font-medium">QR unavailable</p>
                      <p className="mt-1 text-[12px] leading-5">Create a replacement before printing or sharing.</p>
                    </div>
                  </div>
                  <p className="text-[13px] font-medium text-foreground">Customer QR</p>
                  <p className="text-[12px] text-muted-foreground">This QR is hidden because the public link cannot validate safely.</p>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
      <Dialog open={!!pendingStatusChange} onOpenChange={(open) => !open && setPendingStatusChange(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingStatusChange?.status === 'active' ? 'Resume AWA Chat?' : 'Pause AWA Chat?'}</DialogTitle>
            <DialogDescription>
              {pendingStatusChange?.status === 'active'
                ? 'Customers will be able to use this public chat link again.'
                : 'Customers who open this public chat link may not be able to start new conversations until it is resumed.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingStatusChange(null)}>Cancel</Button>
            <Button
              type="button"
              onClick={() => {
                if (pendingStatusChange) {
                  handleStatus(pendingStatusChange.link, pendingStatusChange.status);
                }
                setPendingStatusChange(null);
              }}
              disabled={updateLink.isPending}
            >
              {pendingStatusChange?.status === 'active' ? 'Resume Chat' : 'Pause Chat'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmActionDialog
        open={rotateConfirmOpen}
        onOpenChange={setRotateConfirmOpen}
        title="Rotate Public Chat QR / Link?"
        description="This will permanently invalidate the old link and QR code. Customers using the old link will be blocked."
        confirmLabel="Rotate Link"
        variant="warning"
        isLoading={rotateLink.isPending}
        onConfirm={async () => {
          if (!activeLink) return;
          try {
            await rotateLink.mutateAsync(activeLink.id);
            toast.success('Public chat link rotated successfully!');
            setRotateConfirmOpen(false);
          } catch (error) {
            toast.error(getApiErrorMessage(error, 'Could not rotate link'));
          }
        }}
      />
    </div>
  );
}



function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-[14px] font-semibold capitalize text-foreground">{value}</p>
    </div>
  );
}

function normalizeLocalPublicChatUrl(publicUrl?: string | null) {
  if (!publicUrl || typeof window === 'undefined') {
    return publicUrl || '';
  }
  try {
    const parsed = new URL(publicUrl);
    const current = new URL(window.location.href);
    const localHosts = new Set(['localhost', '127.0.0.1']);
    if (
      localHosts.has(parsed.hostname) &&
      localHosts.has(current.hostname) &&
      parsed.port === current.port &&
      parsed.pathname.startsWith('/chat/')
    ) {
      return `${current.origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
  } catch {
    return publicUrl;
  }
  return publicUrl;
}
