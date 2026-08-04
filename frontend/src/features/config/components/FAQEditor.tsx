import { useState } from 'react';
import { useFormContext, useFieldArray } from 'react-hook-form';
import type { TenantConfig } from '@/types/config.types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Trash2, Plus } from 'lucide-react';
import { v4 as uuidv4 } from 'uuid';
export function FAQEditor() {
  const { register, control } = useFormContext<TenantConfig>();
  const [pendingRemoveIndex, setPendingRemoveIndex] = useState<number | null>(null);
  const { fields, append, remove } = useFieldArray({
    control,
    name: "faqs",
    keyName: "fieldId"
  });
  return (
    <div className="w-full max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">FAQs AWA can answer</h3>
          <p className="text-sm text-muted-foreground">Add common customer questions and approved answers. AWA uses these directly in customer conversations.</p>
        </div>
        <Button
          type="button"
          variant="default"
          onClick={() => append({ id: uuidv4(), question: '', answer: '' })}
        >
          <Plus className="w-4 h-4 mr-2" /> Add FAQ
        </Button>
      </div>
      {fields.length === 0 ? (
        <div className="p-8 border border-dashed rounded-lg text-center bg-card/50">
           <p className="text-sm text-muted-foreground">You currently have no FAQs defined.</p>
           <p className="text-xs text-muted-foreground mt-1">Add Q&A pairs to help AWA answer common questions about your business.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {fields.map((field, index) => (
            <div key={field.fieldId} className="p-4 border rounded-lg bg-card relative group flex gap-3">
              <input type="hidden" {...register(`faqs.${index}.id` as const)} />
              <div className="min-w-0 flex-1 space-y-4">
                <div className="space-y-2">
                  <Input
                    {...register(`faqs.${index}.question` as const)}
                    placeholder="Question (e.g. Do you accept walk-ins?)"
                    className="font-medium"
                  />
                </div>
                <div className="space-y-2">
                   <Textarea
                     {...register(`faqs.${index}.answer` as const)}
                     placeholder="Answer (e.g. Yes! Walk-ins are always welcome...)"
                     rows={3}
                     className="resize-none"
                   />
                </div>
              </div>
              <div className="flex items-start">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground hover:text-destructive transition-colors mt-1 hover:bg-destructive/10"
                  onClick={() => setPendingRemoveIndex(index)}
                  aria-label="Remove FAQ"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Dialog open={pendingRemoveIndex !== null} onOpenChange={(open) => !open && setPendingRemoveIndex(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove FAQ?</DialogTitle>
            <DialogDescription>
              This removes the FAQ from the setup form. Save Business Setup afterwards to remove it from customer-facing AI answers.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingRemoveIndex(null)}>Cancel</Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                if (pendingRemoveIndex !== null) {
                  remove(pendingRemoveIndex);
                }
                setPendingRemoveIndex(null);
              }}
            >
              Remove FAQ
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}