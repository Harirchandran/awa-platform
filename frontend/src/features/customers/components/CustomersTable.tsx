import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useAddCustomerTag,
  useCreateCustomerNote,
  useCreateCustomerTag,
  useCustomer,
  useCustomerNotes,
  useCustomerTags,
  useCustomerTimeline,
  useDeleteCustomerNote,
  useRemoveCustomerTag,
  useUpdateCustomer,
  useMergeCustomer,
} from '../api';
import type { Customer, CustomerNote, CustomerTag, CustomerTimelineItem } from '../api';
import { format } from 'date-fns';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'react-hot-toast';
import { ArrowRight, Clock, Loader2, MessageSquare, NotebookPen, Tag, Users, X } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { getApiErrorMessage } from '@/lib/api-errors';
import { DetailSheetSkeleton, TableSkeleton } from '@/components/ui/loading-states';
import { cn } from '@/lib/utils';
interface CustomersTableProps {
  customers: Customer[];
  isLoading: boolean;
  isFetching?: boolean;
  hasNextPage?: boolean;
  fetchNextPage: () => void;
  isFetchingNextPage: boolean;
}
interface CustomerFormValues {
  name: string;
  email: string;
  consent_status: 'pending' | 'granted' | 'withdrawn';
}
const TAG_COLORS = ['#0f766e', '#2563eb', '#7c3aed', '#be123c', '#ca8a04', '#475569'];
function maskPhoneNumber(phone: string) {
  if (!phone || phone.length < 8) {
    return phone || 'Browser chat';
  }
  const start = phone.slice(0, 6);
  const end = phone.slice(-3);
  return `${start}*****${end}`;
}
function formatCustomerDate(value: string | undefined | null, pattern: string): string {
  if (!value) {
    return 'Not available';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not available' : format(parsed, pattern);
}
function formatMoney(value: number): string {
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(value || 0);
}
function TagPill({ tag, onRemove }: { tag: CustomerTag; onRemove?: () => void }) {
  return (
    <span
      className="inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium"
      style={{ borderColor: `${tag.color}55`, backgroundColor: `${tag.color}18`, color: tag.color }}
    >
      <span className="truncate">{tag.name}</span>
      {onRemove && (
        <button type="button" className="rounded-sm p-0.5 hover:bg-background/70" onClick={onRemove} aria-label={`Remove ${tag.name}`}>
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}
export function CustomersTable({ customers, isLoading, isFetching = false, hasNextPage, fetchNextPage, isFetchingNextPage }: CustomersTableProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  if (isLoading) {
    return <TableSkeleton columns={6} rows={5} />;
  }
  if (!customers || customers.length === 0) {
    return (
      <div className="rounded-lg border bg-card/70 p-8 text-center sm:p-12">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-primary/20 bg-primary/10">
          <Users className="h-5 w-5 text-primary" />
        </div>
        <h3 className="mt-4 text-[16px] font-semibold">No customers yet</h3>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          Customers are created automatically from AWA Chat, WhatsApp, and contact events. Customer records help connect chats, bookings, orders, reminders, and reviews when those workflows are available.
        </p>
        <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
          <Button asChild size="sm">
            <Link to="/dashboard/awa-chat">
              Open AWA Chat
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/dashboard/conversations">
              <MessageSquare className="h-3.5 w-3.5" />
              View Conversations
            </Link>
          </Button>
        </div>
      </div>
    );
  }
  return (
    <>
      <div className={cn("overflow-hidden rounded-md border bg-card transition-opacity duration-200", isFetching && !isLoading && "opacity-50 pointer-events-none")}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Customer</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead>Last Interaction</TableHead>
              <TableHead>Conversations</TableHead>
              <TableHead>Bookings</TableHead>
              <TableHead>Orders</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {customers.map((customer) => (
              <TableRow
                key={customer.id}
                className="cursor-pointer hover:bg-muted/50"
                onClick={() => setSelectedId(customer.id)}
              >
                <TableCell>
                  <div className="font-medium">{customer.name || 'Anonymous'}</div>
                  <div className="text-xs text-muted-foreground">{maskPhoneNumber(customer.phone_number)}</div>
                </TableCell>
                <TableCell>
                  <div className="flex max-w-[220px] flex-wrap gap-1">
                    {customer.tags.length > 0 ? customer.tags.slice(0, 3).map((tag) => <TagPill key={tag.id} tag={tag} />) : (
                      <span className="text-xs text-muted-foreground">No tags</span>
                    )}
                    {customer.tags.length > 3 && <span className="text-xs text-muted-foreground">+{customer.tags.length - 3}</span>}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">{formatCustomerDate(customer.last_interaction, 'MMM d')}</TableCell>
                <TableCell>{customer.conversation_count}</TableCell>
                <TableCell>{customer.booking_count}</TableCell>
                <TableCell>
                  <div>{customer.order_count}</div>
                  {customer.total_order_value > 0 && (
                    <div className="text-xs text-muted-foreground">Rs {formatMoney(customer.total_order_value)}</div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasNextPage && (
        <div className="flex justify-center pt-4">
          <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
            {isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isFetchingNextPage ? 'Loading more' : 'Load More Customers'}
          </Button>
        </div>
      )}
      <Sheet open={!!selectedId} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent className="overflow-y-auto sm:max-w-2xl">
          {selectedId && <CustomerDetailForm customerId={selectedId} onDone={() => setSelectedId(null)} />}
        </SheetContent>
      </Sheet>
    </>
  );
}
function CustomerDetailForm({ customerId, onDone }: { customerId: string; onDone: () => void }) {
  const { data: customer, isLoading: isFetching } = useCustomer(customerId);
  const { data: allTags = [] } = useCustomerTags();
  const { data: notes = [], isLoading: isLoadingNotes } = useCustomerNotes(customerId);
  const { data: timeline = [], isLoading: isLoadingTimeline } = useCustomerTimeline(customerId);
  const updateMutation = useUpdateCustomer();
  const createTagMutation = useCreateCustomerTag();
  const addTagMutation = useAddCustomerTag();
  const removeTagMutation = useRemoveCustomerTag();
  const createNoteMutation = useCreateCustomerNote();
  const deleteNoteMutation = useDeleteCustomerNote();
  const mergeMutation = useMergeCustomer();
  const [pendingFormData, setPendingFormData] = useState<CustomerFormValues | null>(null);
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false);
  const [secondaryIdInput, setSecondaryIdInput] = useState('');
  const [selectedTagId, setSelectedTagId] = useState('');
  const [newTagName, setNewTagName] = useState('');
  const [newTagColor, setNewTagColor] = useState(TAG_COLORS[0]);
  const [noteText, setNoteText] = useState('');
  const { register, handleSubmit, reset, setValue } = useForm<CustomerFormValues>({
    defaultValues: {
      name: '',
      email: '',
      consent_status: 'pending',
    },
  });
  useEffect(() => {
    if (!customer) {
      return;
    }
    reset({
      name: customer.name || '',
      email: customer.email || '',
      consent_status: customer.consent_status || 'pending',
    });
  }, [customer, reset]);
  const availableTags = useMemo(() => {
    const applied = new Set((customer?.tags ?? []).map((tag) => tag.id));
    return allTags.filter((tag) => !applied.has(tag.id));
  }, [allTags, customer?.tags]);
  if (isFetching || !customer) {
    return (
      <>
        <SheetHeader className="mb-6">
          <SheetTitle>Customer Profile</SheetTitle>
          <SheetDescription>Loading customer details</SheetDescription>
        </SheetHeader>
        <DetailSheetSkeleton />
      </>
    );
  }
  const saveCustomer = (formData: CustomerFormValues) => {
    updateMutation.mutate(
      { id: customerId, data: formData },
      {
        onSuccess: () => {
          toast.success('Profile updated successfully');
          setPendingFormData(null);
          onDone();
        },
        onError: (error: unknown) => {
          toast.error(getApiErrorMessage(error, 'Update failed'));
        },
      }
    );
  };
  const addExistingTag = () => {
    if (!selectedTagId) {
      return;
    }
    addTagMutation.mutate(
      { customerId, tagId: selectedTagId },
      {
        onSuccess: () => {
          toast.success('Tag added');
          setSelectedTagId('');
        },
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not add tag')),
      }
    );
  };
  const createAndAddTag = () => {
    const name = newTagName.trim();
    if (!name) {
      return;
    }
    createTagMutation.mutate(
      { name, color: newTagColor },
      {
        onSuccess: (tag) => {
          setNewTagName('');
          addTagMutation.mutate(
            { customerId, tagId: tag.id },
            {
              onSuccess: () => toast.success('Tag created and added'),
              onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Created tag, but could not attach it')),
            }
          );
        },
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not create tag')),
      }
    );
  };
  const createNote = () => {
    const note = noteText.trim();
    if (!note) {
      return;
    }
    createNoteMutation.mutate(
      { customerId, note },
      {
        onSuccess: () => {
          toast.success('Note added');
          setNoteText('');
        },
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not add note')),
      }
    );
  };
  return (
    <>
      <SheetHeader className="mb-5">
        <SheetTitle>{customer.name || 'Customer Profile'}</SheetTitle>
        <SheetDescription>{maskPhoneNumber(customer.phone_number)}</SheetDescription>
      </SheetHeader>
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Conversations" value={customer.conversation_count} />
        <Metric label="Bookings" value={customer.booking_count} />
        <Metric label="Orders" value={customer.order_count} />
        <Metric label="Order value" value={`Rs ${formatMoney(customer.total_order_value)}`} />
      </div>
      <Tabs defaultValue="timeline" className="space-y-5">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="crm">CRM</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
        </TabsList>
        <TabsContent value="profile">
          <form onSubmit={handleSubmit((formData) => setPendingFormData(formData))} className="space-y-6">
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Full Name</Label>
                <Input id="name" placeholder="John Doe" {...register('name')} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email Address</Label>
                <Input id="email" type="email" placeholder="john@example.com" {...register('email')} />
              </div>
              <div className="space-y-2">
                <Label>Consent Status</Label>
                <Select
                  defaultValue={customer.consent_status || 'pending'}
                  onValueChange={(value: CustomerFormValues['consent_status']) => setValue('consent_status', value)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select consent" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pending">Pending</SelectItem>
                    <SelectItem value="granted">Granted</SelectItem>
                    <SelectItem value="withdrawn">Withdrawn</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="border-t border-border pt-4">
              <h4 className="mb-3 text-sm font-semibold">AI Context Metadata</h4>
              <div className="break-all rounded-md bg-muted p-3 font-mono text-xs text-muted-foreground whitespace-pre-wrap">
                {JSON.stringify(customer.metadata || {}, null, 2)}
              </div>
            </div>
            <Button type="submit" className="w-full" disabled={updateMutation.isPending}>
              {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Review & Save
            </Button>
          </form>
          <div className="border-t border-border mt-6 pt-4">
            <Button
              type="button"
              variant="destructive"
              className="w-full"
              onClick={() => {
                setSecondaryIdInput('');
                setMergeDialogOpen(true);
              }}
            >
              Merge Duplicate Profile
            </Button>
          </div>
        </TabsContent>
        <TabsContent value="crm" className="space-y-6">
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <Tag className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-semibold">Tags</h4>
            </div>
            <div className="flex flex-wrap gap-2">
              {customer.tags.length > 0 ? customer.tags.map((tag) => (
                <TagPill
                  key={tag.id}
                  tag={tag}
                  onRemove={() => removeTagMutation.mutate(
                    { customerId, tagId: tag.id },
                    {
                      onSuccess: () => toast.success('Tag removed'),
                      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not remove tag')),
                    }
                  )}
                />
              )) : (
                <p className="text-sm text-muted-foreground">No tags yet.</p>
              )}
            </div>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <Select value={selectedTagId} onValueChange={setSelectedTagId} disabled={availableTags.length === 0}>
                <SelectTrigger>
                  <SelectValue placeholder={availableTags.length ? 'Add existing tag' : 'No available tags'} />
                </SelectTrigger>
                <SelectContent>
                  {availableTags.map((tag) => (
                    <SelectItem key={tag.id} value={tag.id}>{tag.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button type="button" variant="outline" onClick={addExistingTag} disabled={!selectedTagId || addTagMutation.isPending}>
                Add
              </Button>
            </div>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
              <Input placeholder="Create tag" value={newTagName} onChange={(event) => setNewTagName(event.target.value)} />
              <Select value={newTagColor} onValueChange={setNewTagColor}>
                <SelectTrigger className="sm:w-[118px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TAG_COLORS.map((color) => (
                    <SelectItem key={color} value={color}>{color}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button type="button" onClick={createAndAddTag} disabled={!newTagName.trim() || createTagMutation.isPending}>
                Create
              </Button>
            </div>
          </section>
          <section className="space-y-3 border-t border-border pt-5">
            <div className="flex items-center gap-2">
              <NotebookPen className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-semibold">Internal Notes</h4>
            </div>
            <Textarea
              value={noteText}
              onChange={(event) => setNoteText(event.target.value)}
              placeholder="Add context your team should remember"
              className="min-h-[96px]"
            />
            <Button type="button" onClick={createNote} disabled={!noteText.trim() || createNoteMutation.isPending}>
              {createNoteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Add Note
            </Button>
            <NoteList
              notes={notes}
              isLoading={isLoadingNotes}
              onDelete={(noteId) => deleteNoteMutation.mutate(
                { customerId, noteId },
                {
                  onSuccess: () => toast.success('Note deleted'),
                  onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not delete note')),
                }
              )}
            />
          </section>
        </TabsContent>
        <TabsContent value="timeline">
          <Timeline items={timeline} isLoading={isLoadingTimeline} />
        </TabsContent>
      </Tabs>
      <Dialog open={!!pendingFormData} onOpenChange={(open) => !open && setPendingFormData(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save customer profile?</DialogTitle>
            <DialogDescription>
              This updates stored customer details and consent status for this business. Confirm only after checking the values.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingFormData(null)} disabled={updateMutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => pendingFormData && saveCustomer(pendingFormData)}
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save Profile
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={mergeDialogOpen} onOpenChange={setMergeDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Merge Duplicate Customer Profile</DialogTitle>
            <DialogDescription className="text-red-600 dark:text-red-400 font-medium">
              WARNING: This action is destructive and cannot be undone.
              All conversations, bookings, orders, and notes of the duplicate customer (secondary) will be moved to this profile (primary).
              The duplicate profile will then be removed from the application's active customer records.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="secondary-customer-id">Secondary Customer ID</Label>
              <Input
                id="secondary-customer-id"
                placeholder="e.g. 123e4567-e89b-12d3-a456-426614174000"
                value={secondaryIdInput}
                onChange={(e) => setSecondaryIdInput(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setMergeDialogOpen(false)} disabled={mergeMutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                const secId = secondaryIdInput.trim();
                if (!secId) {
                  toast.error('Please enter a valid secondary customer ID');
                  return;
                }
                mergeMutation.mutate(
                  { primaryId: customerId, secondaryId: secId },
                  {
                    onSuccess: () => {
                      toast.success('Customers merged successfully');
                      setMergeDialogOpen(false);
                      onDone();
                    },
                    onError: (error: unknown) => {
                      toast.error(getApiErrorMessage(error, 'Merge failed'));
                    },
                  }
                );
              }}
              disabled={mergeMutation.isPending || !secondaryIdInput.trim()}
            >
              {mergeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Confirm Merge
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  );
}
function NoteList({ notes, isLoading, onDelete }: { notes: CustomerNote[]; isLoading: boolean; onDelete: (noteId: string) => void }) {
  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading notes...</p>;
  }
  if (notes.length === 0) {
    return <p className="text-sm text-muted-foreground">No internal notes yet.</p>;
  }
  return (
    <div className="space-y-2">
      {notes.map((note) => (
        <div key={note.id} className="rounded-md border p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[12px] text-muted-foreground">
                {note.author_email || 'Team note'} - {formatCustomerDate(note.created_at, 'MMM d, h:mm a')}
              </p>
              <p className="mt-1 whitespace-pre-wrap text-sm">{note.note}</p>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={() => onDelete(note.id)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
function Timeline({ items, isLoading }: { items: CustomerTimelineItem[]; isLoading: boolean }) {
  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading timeline...</p>;
  }
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">No timeline events yet.</p>;
  }
  return (
    <div className="space-y-3">
      {items.map((item) => (
        <div key={`${item.event_type}-${item.ref_id}`} className="grid grid-cols-[28px_1fr] gap-3">
          <div className="mt-1 flex h-7 w-7 items-center justify-center rounded-md border bg-muted">
            <Clock className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <div className="rounded-md border p-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-semibold">{item.title}</p>
              <p className="text-[12px] text-muted-foreground">{formatCustomerDate(item.occurred_at, 'MMM d, h:mm a')}</p>
            </div>
            {item.subtitle && <p className="mt-1 text-sm text-muted-foreground">{item.subtitle}</p>}
            <p className="mt-2 text-[11px] uppercase text-muted-foreground">{item.event_type}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
