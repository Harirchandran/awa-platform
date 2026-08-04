import { expect, test, type Page } from '@playwright/test';
import {
  getPublicChatPollingInterval,
  PENDING_REPLY_POLL_MS,
  POLLING_FALLBACK_MS,
  SSE_SAFETY_POLL_MS,
} from '../../src/routes/public/public-chat-polling';

function wrapResponse(payload: unknown) {
  return JSON.stringify({
    success: true,
    data: payload,
    timestamp: new Date().toISOString(),
    request_id: 'req-public-chat-reliability',
  });
}

type ReliabilityScenario = {
  messages: Array<Record<string, unknown>>;
  sendSse: (message: Record<string, unknown>) => void;
};

async function setupPublicChat(
  page: Page,
  initialStatus: 'active' | 'escalated' = 'active',
): Promise<ReliabilityScenario> {
  await page.addInitScript(() => {
    sessionStorage.setItem('awa_public_chat_reliability-test', JSON.stringify({
      id: 'session-reliability-test',
      session_token: 'session-token',
      tenant_name: 'Test Clinic',
      display_name: 'Test Clinic',
      conversation_id: 'conversation-reliability-test',
    }));
  });

  await page.route('**/api/v1/public-chat/links/reliability-test', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        slug: 'reliability-test',
        display_name: 'Test Clinic',
        tenant_name: 'Test Clinic',
        status: 'active',
        privacy_note: 'Test privacy note',
        suggested_replies: [],
        captcha_required: false,
        captcha_mode: 'disabled',
        customer_otp_required: false,
      }),
    });
  });

  const messages: Array<Record<string, unknown>> = [{
    id: 'welcome-message',
    direction: 'outbound',
    message_text: 'How can I help?',
    created_at: '2026-07-16T01:00:00.000Z',
    conversation_status: initialStatus,
  }];
  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/messages/list', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse(messages),
    });
  });

  let releaseSse: ((message: Record<string, unknown>) => void) | null = null;
  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/events?token=*', async (route) => {
    const message = await new Promise<Record<string, unknown>>((resolve) => {
      releaseSse = resolve;
    });
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache' },
      body: `data: ${JSON.stringify(message)}\n\n`,
    });
  });

  await page.goto('/chat/reliability-test');
  await expect(page.getByLabel('Type your message')).toBeVisible();
  await expect.poll(() => Boolean(releaseSse)).toBe(true);

  return {
    messages,
    sendSse: (message) => {
      const receiver = releaseSse;
      releaseSse = null;
      receiver?.(message);
    },
  };
}

test('polling stays fast for a pending reply and remains a safety net while SSE is active', () => {
  expect(getPublicChatPollingInterval(true, false)).toBe(PENDING_REPLY_POLL_MS);
  expect(getPublicChatPollingInterval(true, true)).toBe(PENDING_REPLY_POLL_MS);
  expect(getPublicChatPollingInterval(false, false)).toBe(POLLING_FALLBACK_MS);
  expect(getPublicChatPollingInterval(false, true)).toBe(SSE_SAFETY_POLL_MS);
});

test('a failed end request keeps the live chat open and explains the failure', async ({ page }) => {
  await setupPublicChat(page);
  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/end', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        success: false,
        error: { code: 'end_unavailable', message: 'End service unavailable. Please try again.' },
      }),
    });
  });

  await page.getByRole('button', { name: 'End', exact: true }).click();

  const endError = page.getByRole('alert').filter({ hasText: 'End service unavailable. Please try again.' });
  await expect(endError).toBeVisible();
  await expect(endError).toHaveAttribute('aria-live', 'assertive');
  await expect(page.getByRole('heading', { name: 'Chat ended' })).toBeHidden();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});

test('a failed send retries with the same client id and renders the accepted customer message once', async ({ page }) => {
  const clientMessageIds: string[] = [];
  let scenario: ReliabilityScenario | null = null;
  let markFirstRequestStarted: (() => void) | null = null;
  let releaseFirstResponse: (() => void) | null = null;
  const firstRequestStarted = new Promise<void>((resolve) => {
    markFirstRequestStarted = resolve;
  });
  const firstResponseGate = new Promise<void>((resolve) => {
    releaseFirstResponse = resolve;
  });

  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/messages', async (route) => {
    const payload = route.request().postDataJSON();
    clientMessageIds.push(String(payload?.client_message_id || ''));
    if (clientMessageIds.length === 1) {
      scenario?.messages.push({
        id: 'accepted-customer-message',
        direction: 'inbound',
        message_text: payload?.message_text,
        created_at: '2026-07-16T01:02:00.000Z',
        conversation_status: 'active',
      });
      markFirstRequestStarted?.();
      await firstResponseGate;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          error: {
            code: 'send_result_unknown',
            message: 'Message delivery status is unknown. Retry safely.',
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        message_id: 'accepted-customer-message',
        conversation_id: 'conversation-reliability-test',
        status: 'duplicate',
        processing_status: 'ignored',
        duplicate: true,
      }),
    });
  });

  scenario = await setupPublicChat(page);
  const connectionStatus = page.getByRole('status').filter({ hasText: 'Reconnecting' });
  await expect(connectionStatus).toHaveAttribute('aria-live', 'polite');

  const customerText = 'Please book the 3 PM appointment.';
  await page.getByLabel('Type your message').fill(customerText);
  await page.getByRole('button', { name: 'Send message' }).click();
  await firstRequestStarted;
  const sendingStatus = page.getByRole('status').filter({ hasText: 'Sending message.' });
  await expect(sendingStatus).toBeVisible();
  await expect(sendingStatus).toHaveAttribute('aria-live', 'polite');
  releaseFirstResponse?.();

  const sendError = page.getByRole('alert').filter({ hasText: 'Message delivery status is unknown. Retry safely.' });
  await expect(sendError).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retry', exact: true }).click();

  await expect.poll(() => clientMessageIds.length).toBe(2);
  expect(clientMessageIds[0]).not.toBe('');
  expect(clientMessageIds[1]).toBe(clientMessageIds[0]);
  await expect(page.getByRole('status').filter({ hasText: 'Message already received. No duplicate was sent.' })).toBeVisible();
  await expect(page.getByText(customerText, { exact: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeHidden();
});

test('WhatsApp follow-up consent starts unchecked and is saved as false without explicit opt-in', async ({ page }) => {
  let contactPayload: Record<string, unknown> | null = null;
  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/contact', async (route) => {
    contactPayload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({ saved: true, email_saved: false, phone_saved: true }),
    });
  });
  await setupPublicChat(page, 'escalated');

  const whatsappConsent = page.getByRole('checkbox', { name: /WhatsApp updates and reminders/i });
  await expect(whatsappConsent).not.toBeChecked();
  await page.getByLabel('Phone').fill('+91 98765 43210');
  await page.getByRole('button', { name: 'Save contact' }).click();

  await expect.poll(() => contactPayload).not.toBeNull();
  expect(contactPayload?.whatsapp_opted_in).toBe(false);
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
});

test('human handoff keeps the composer available and retains cancellation', async ({ page }) => {
  let sentMessage = '';
  await page.route('**/api/v1/public-chat/sessions/session-reliability-test/messages', async (route) => {
    sentMessage = String(route.request().postDataJSON()?.message_text || '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        message_id: 'customer-context',
        conversation_id: 'conversation-reliability-test',
        status: 'queued',
        processing_status: 'queued',
      }),
    });
  });
  await setupPublicChat(page, 'escalated');

  await expect(page.getByRole('heading', { name: 'Assigned to Human Team' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel Handoff' })).toBeVisible();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
  await expect(page.getByLabel('Type your message')).toHaveAttribute('placeholder', 'Add context for the team');

  await page.getByLabel('Type your message').fill('I can also come tomorrow afternoon.');
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect.poll(() => sentMessage).toBe('I can also come tomorrow afternoon.');
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});

test('an SSE message can move the conversation into human handoff without hiding the composer', async ({ page }) => {
  const scenario = await setupPublicChat(page);
  const escalatedMessage = {
    id: 'handoff-message',
    direction: 'outbound',
    message_text: 'I have assigned this to our team.',
    created_at: '2026-07-16T01:01:00.000Z',
    conversation_status: 'escalated',
  };
  scenario.messages.push(escalatedMessage);
  scenario.sendSse({
    event_type: 'message',
    message_id: escalatedMessage.id,
    role: 'assistant',
    content: escalatedMessage.message_text,
    created_at: escalatedMessage.created_at,
    conversation_status: 'escalated',
  });

  await expect(page.getByRole('heading', { name: 'Assigned to Human Team' })).toBeVisible();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});
