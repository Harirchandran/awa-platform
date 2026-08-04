import { test, expect, type Page, type BrowserContext, type TestInfo } from '@playwright/test';

// Helper to wrap standard API responses
function wrapResponse(payload: unknown) {
  return JSON.stringify({
    success: true,
    data: payload,
    timestamp: new Date().toISOString(),
    request_id: 'req-mock-e2e',
  });
}

// Setup all API interceptions to keep E2E tests fully mock-based and self-contained
async function setupApiIntercepts(context: BrowserContext) {
  // Auth and Profile
  await context.route('**/api/v1/auth/login', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock_token:tenant_admin',
        dev_session_token: 'mock_token:tenant_admin',
        tenant_id: 'tenant-1',
        role: 'tenant_admin',
      }),
    });
  });

  await context.route('**/api/v1/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        id: 'user-1',
        email: 'admin@tenant.com',
        name: 'Tenant Administrator',
        role: 'tenant_admin',
      }),
    });
  });

  // MUST MOCK /api/v1/tenant so useCurrentUser() hook does not crash and redirect to /login
  await context.route('**/api/v1/tenant', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        id: 'tenant-1',
        name: 'SmileDent Dental Clinic',
        ai_mode: 'standard',
      }),
    });
  });

  // Config/Business Setup
  await context.route('**/api/v1/config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        business_name: 'SmileDent Dental Clinic',
        business_type: 'dentist',
        ai_mode: 'standard',
        ai_tone: 'friendly and professional',
        timezone: 'America/New_York',
        default_language: 'en',
        greeting_message: 'Hello! Welcome to SmileDent.',
        working_hours: {
          monday: { enabled: true, slots: [{ start: '09:00', end: '17:00' }] },
          tuesday: { enabled: true, slots: [{ start: '09:00', end: '17:00' }] },
          wednesday: { enabled: true, slots: [{ start: '09:00', end: '17:00' }] },
          thursday: { enabled: true, slots: [{ start: '09:00', end: '17:00' }] },
          friday: { enabled: true, slots: [{ start: '09:00', end: '17:00' }] },
        },
        faqs: [],
        services: [
          { id: 'srv-1', name: 'Dental Cleaning', price: 10000, duration: 45 },
          { id: 'srv-2', name: 'Tooth Extraction', price: 25000, duration: 60 },
        ],
        modules: {
          booking: { enabled: true },
          order: { enabled: true },
          payments: { enabled: true },
          reminders: { enabled: true },
        },
        booking_settings: {
          buffer_before_mins: 0,
          buffer_after_mins: 0,
          max_bookings_per_day: null,
          cancellation_cutoff_minutes: 120,
          reminder_offsets_minutes: [1440, 60],
          approval_required_for: [],
        },
      }),
    });
  });

  // Workflow Tools API
  await context.route('**/api/v1/tools', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        tenant: {
          id: 'tenant-1',
          name: 'SmileDent Dental Clinic',
          slug: 'smiledent',
          status: 'active',
          plan_code: 'standard',
        },
        config_version: 1,
        business_type: 'dentist',
        tools: [
          {
            key: 'booking',
            label: 'Bookings',
            category: 'workflow',
            description: 'Schedule dentist appointments and checkouts.',
            enabled: true,
            plan_allowed: true,
            field_count: 0,
            locked_reason: null,
            icon_key: 'calendar_check',
            route_path: '/dashboard/bookings',
          },
          {
            key: 'order',
            label: 'Orders',
            category: 'workflow',
            description: 'Manage dentistry products and services order forms.',
            enabled: true,
            plan_allowed: true,
            field_count: 0,
            locked_reason: null,
            icon_key: 'shopping_bag',
            route_path: '/dashboard/orders',
          },
          {
            key: 'payments',
            label: 'Payments',
            category: 'workflow',
            description: 'Process tooth treatments billing and custom invoices.',
            enabled: true,
            plan_allowed: true,
            field_count: 0,
            locked_reason: null,
            icon_key: 'wallet',
            route_path: '/dashboard/payments',
          },
          {
            key: 'reminders',
            label: 'Reminders',
            category: 'workflow',
            description: 'Automate dental checkup campaign messaging.',
            enabled: true,
            plan_allowed: true,
            field_count: 0,
            locked_reason: null,
            icon_key: 'bell',
            route_path: '/dashboard/reminders',
          },
        ],
        active_modules: ['booking', 'order', 'payments', 'reminders'],
        plan_policy: {
          plan_code: 'standard',
          allowed_ai_modes: ['standard'],
          allowed_modules: ['booking', 'order', 'payments', 'reminders'],
        },
      }),
    });
  });

  // Billing API
  await context.route('**/api/v1/billing/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        tenant_billing: {
          status: 'trial',
          plan_code: 'standard',
          trial_ends_at: '2026-12-31T00:00:00Z',
        },
        available_plans: [],
        current_plan: {
          id: 'plan-standard',
          plan_code: 'standard',
          name: 'Standard Plan',
          monthly_price_cents: 2900,
          currency: 'USD',
          razorpay_plan_id: '',
          features_json: ['Standard Automation', 'Email Notifications'],
        },
        billing_mode: 'mock',
        mock_billing: true,
        usage: {
          today_message_count: 5,
          month_message_count: 125,
          month_ai_tokens_used: 45000,
          month_credits_used: 12.5,
        },
      }),
    });
  });

  // Feature Flags API
  await context.route('**/api/v1/feature-flags', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          key: 'smart_reminders',
          description: 'Enable AI-driven smart reminder logic.',
          enabled: true,
          updated_at: '2026-06-17T10:00:00Z',
        },
        {
          key: 'beta_features',
          description: 'Access beta user testing modules.',
          enabled: false,
          updated_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Catalog categories and items
  await context.route('**/api/v1/catalog/categories**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'cat-1',
          tenant_id: 'tenant-1',
          name: 'Orthodontics',
          description: 'Braces and aligners',
          sort_order: 1,
          is_active: true,
          item_count: 1,
          metadata: {},
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  await context.route('**/api/v1/catalog/items**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'item-1',
          name: 'Orthodontic Braces',
          sku: 'SKU-BRACES-01',
          price: 150000,
          currency: 'USD',
          category_name: 'Orthodontics',
          is_active: true,
          is_available: true,
          stock_tracking_enabled: true,
          stock_quantity: 10,
          low_stock_threshold: 2,
          low_stock: false,
          metadata: {},
          created_at: '2026-06-17T10:00:00Z',
        },
        {
          id: 'item-2',
          name: 'Teeth Whitening Kit',
          sku: 'SKU-WHITEN-02',
          price: 4500,
          currency: 'USD',
          category_name: 'Cosmetic',
          is_active: true,
          is_available: true,
          stock_tracking_enabled: true,
          stock_quantity: 50,
          low_stock_threshold: 5,
          low_stock: false,
          metadata: {},
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Conversations
  await context.route('**/api/v1/conversations**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'conv-1',
          customer_id: 'cust-1',
          customer_name: 'John Doe',
          customer_phone: '+15550199',
          customer_email: 'john.doe@example.com',
          status: 'escalated',
          workflow_state: 'human_handoff',
          ai_mode: 'standard',
          channel_type: 'awa_chat',
          escalation_reason: 'Requested human operator',
          escalated_at: '2026-06-17T12:00:00Z',
          last_message_at: '2026-06-17T12:00:00Z',
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Bookings list
  await context.route('**/api/v1/bookings**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'booking-1',
          customer_id: 'cust-1',
          customer_name: 'John Doe',
          customer_phone: '+15550199',
          service_type: 'dentist',
          service_name: 'Dental Cleaning',
          booking_datetime: '2026-06-19T10:00:00Z',
          status: 'confirmed',
          collected_fields: {},
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Leads list
  await context.route('**/api/v1/leads**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'lead-1',
          tenant_id: 'tenant-1',
          customer_id: 'cust-1',
          conversation_id: 'conv-1',
          source_channel: 'chat',
          name: 'Jane Smith',
          phone: '+15559876',
          email: 'jane.smith@example.com',
          interest: 'Teeth Whitening Kit',
          status: 'new',
          metadata: {},
          created_at: '2026-06-17T10:00:00Z',
          updated_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Reviews list and settings
  await context.route('**/api/v1/reviews/settings', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        id: 'rev-sett',
        tenant_id: 'tenant-1',
        is_active: true,
        public_review_url: 'https://g.page/smiledent-clinic',
        ask_after_booking_completed: true,
        ask_after_order_completed: true,
        ask_delay_minutes: 60,
        positive_rating_threshold: 4,
        negative_feedback_escalation_enabled: true,
        request_template: 'How was your dental checkup?',
        positive_template: 'Thanks! Please share on Google: {{public_review_url}}',
        negative_template: 'Sorry to hear that. Our operator will reach out.',
        created_at: '2026-06-17T10:00:00Z',
      }),
    });
  });

  await context.route('**/api/v1/reviews*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'review-1',
          tenant_id: 'tenant-1',
          status: 'responded',
          source: 'booking',
          rating: 5,
          feedback_text: 'Excellent service! Very gentle doctor.',
          customer_name: 'John Doe',
          customer_phone: '+15550199',
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Knowledge base list and source summary
  await context.route('**/api/v1/knowledge-base/sources', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          source: 'manual',
          total_count: 2,
          enabled_count: 2,
          disabled_count: 0,
          last_created_at: '2026-06-17T12:00:00Z',
        },
      ]),
    });
  });

  await context.route('**/api/v1/knowledge-base*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'kb-1',
          tenant_id: 'tenant-1',
          document_title: 'Dental Cleaning FAQ',
          chunk_text: 'Dental cleaning removes plaque and tartar. It takes 45 minutes.',
          metadata: { source: 'manual', enabled: true },
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Reminders / scheduled messages
  await context.route('**/api/v1/reminder-rules**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'rule-1',
          tenant_id: 'tenant-1',
          name: 'Post-Appointment Floss Reminder',
          trigger_type: 'after_booking',
          offset_minutes: 1440,
          channel: 'whatsapp',
          template_text: 'Hi {{customer_name}}, make sure to floss today!',
          is_active: true,
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  await context.route('**/api/v1/scheduled-messages**', async (route) => {
    const url = route.request().url();
    if (url.includes('/scheduled-messages/metrics')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: wrapResponse({
          scheduled: 0,
          due: 0,
          failed: 0,
          sent: 0,
          skipped: 0,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([]),
    });
  });

  // AWA Chat public links
  await context.route('**/api/v1/awa-chat/links', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'link-1',
          tenant_id: 'tenant-1',
          display_name: 'SmileDent Chat',
          slug: 'smiledent-chat',
          status: 'active',
          is_publicly_accessible: true,
          public_url: 'http://localhost:5173/chat/smiledent-chat',
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Customers list
  await context.route('**/api/v1/customers**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([
        {
          id: 'cust-1',
          tenant_id: 'tenant-1',
          name: 'John Doe',
          phone: '+15550199',
          email: 'john.doe@example.com',
          created_at: '2026-06-17T10:00:00Z',
        },
      ]),
    });
  });

  // Escalations
  await context.route('**/api/v1/escalations**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([]),
    });
  });

  // WhatsApp accounts
  await context.route('**/api/v1/whatsapp-accounts**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([]),
    });
  });

  // Orders
  await context.route('**/api/v1/orders**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([]),
    });
  });

  // Payments
  await context.route('**/api/v1/payments**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse([]),
    });
  });
}

// Helper to log in using mock auth
async function performLogin(page: Page) {
  await page.goto('/login');
  await page.fill('#email', 'admin@tenant.com');
  await page.fill('#password', 'password123');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard');
}

// Helper to wait for lazy route ready state before capturing screenshots or asserting content
async function waitForRouteReady(page: Page) {
  await page.waitForSelector('#main-content', { state: 'attached', timeout: 8000 });
  // Wait for the skeleton/pulse state to disappear if any
  const skeleton = page.locator('.animate-pulse');
  if (await skeleton.count() > 0) {
    await skeleton.first().waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
  }

  // Wait for title element or container to be visible
  await page.waitForFunction(() => {
    const el = document.querySelector('h1') || document.querySelector('h2') || document.querySelector('#main-content');
    return el && el.getBoundingClientRect().height > 0;
  }, { timeout: 8000 }).catch(() => {});
}

// Capture screenshot and save as test artifact
async function captureScreenshot(page: Page, testInfo: TestInfo, filename: string) {
  await page.screenshot({ path: testInfo.outputPath(filename) });
}

test.describe('Tenant Dashboard - Phase UI-10 E2E & Visual Regression Tests', () => {

  test.beforeEach(async ({ context, page }) => {
    page.on('console', msg => {
      console.log(`BROWSER CONSOLE | [${msg.type()}] | ${msg.text()}`);
    });
    page.on('pageerror', err => {
      console.error(`BROWSER ERROR | ${err.message}\n${err.stack}`);
    });
    await setupApiIntercepts(context);
    await performLogin(page);
  });

  test('UI9-01 Verification: /dashboard/flags loads and sidebar link is visible and active', async ({ page }) => {
    // Navigate directly to flags
    await page.goto('/dashboard/flags');
    await waitForRouteReady(page);

    // Verify Advanced Controls header exists
    await expect(page.locator('main h1:has-text("Advanced Controls")')).toBeVisible();

    const isMobile = page.viewportSize()?.width ? page.viewportSize()!.width < 768 : false;
    if (isMobile) {
      const bottomNav = page.locator('nav.md\\:hidden, div.md\\:hidden >> text=More');
      await bottomNav.first().click();
      await page.waitForTimeout(500);
    }

    // Verify sidebar item exists, matches role permission, is active and has aria-current="page"
    // Note: aria-current is placed on the inner div inside the Link tag
    const sidebarItem = page.locator('aside:visible a[href="/dashboard/flags"] [aria-current="page"]');
    await expect(sidebarItem).toBeVisible();
    await expect(sidebarItem).toHaveAttribute('aria-current', 'page');
  });

  test('UI9-02 Verification: Leads page heading reads Leads and has correct description', async ({ page }) => {
    // Navigate to Leads page
    await page.goto('/dashboard/leads');
    await waitForRouteReady(page);

    // Verify page heading and page layout title resolves to "Leads"
    const pageLayoutTitle = page.locator('header h1');
    await expect(pageLayoutTitle).toHaveText('Leads');

    const mainTitle = page.locator('main h1');
    await expect(mainTitle).toHaveText('Leads');

    // Verify description is updated
    await expect(page.locator('text=Track captured customer interest, follow-up needs, and lead details.')).toBeVisible();
  });

  test('UI9-03 Verification: AWA Chat style preview validates colors but does not claim persistence', async ({ page }) => {
    await page.goto('/dashboard/awa-chat');
    await waitForRouteReady(page);

    const primaryColorInput = page.locator('#theme-primary');
    const saveButton = page.locator('button:has-text("Styling save unavailable")');

    // Styling is preview-only until the backend persists and applies theme values.
    await expect(saveButton).toBeDisabled();
    await expect(saveButton).toHaveAttribute('title', 'Theme preview is not persisted yet');

    // Enter invalid hex
    await primaryColorInput.fill('#123');

    // Check validation text error appears
    await expect(page.locator('text=Use valid hex like #4f46e5')).toBeVisible();

    // Verify button remains disabled while validation is displayed.
    await expect(saveButton).toBeDisabled();

    // Fill valid hex again and verify validation clears but save remains unavailable.
    await primaryColorInput.fill('#4f46e5');
    await expect(page.locator('text=Use valid hex like #4f46e5')).not.toBeVisible();
    await expect(saveButton).toBeDisabled();
  });

  test('Route traversal E2E (All 19 tenant routes)', async ({ page }) => {
    const routes = [
      { path: '/dashboard', heading: 'Overview' },
      { path: '/dashboard/conversations', heading: 'Conversations' },
      { path: '/dashboard/escalations', heading: 'Needs Team Reply' },
      { path: '/dashboard/customers', heading: 'Customers' },
      { path: '/dashboard/leads', heading: 'Leads' },
      { path: '/dashboard/bookings', heading: 'Bookings' },
      { path: '/dashboard/orders', heading: 'Orders' },
      { path: '/dashboard/payments', heading: 'Payments' },
      { path: '/dashboard/reviews', heading: 'Reviews' },
      { path: '/dashboard/knowledge', heading: 'AI Knowledge' },
      { path: '/dashboard/catalog', heading: 'Catalog' },
      { path: '/dashboard/tools', heading: 'Workflow Tools' },
      { path: '/dashboard/reminders', heading: 'Follow-ups' },
      { path: '/dashboard/awa-chat', heading: 'AWA Chat' },
      { path: '/dashboard/whatsapp', heading: 'WhatsApp' },
      { path: '/dashboard/config', heading: 'Business Setup' },
      { path: '/dashboard/billing', heading: 'Billing' },
      { path: '/dashboard/flags', heading: 'Advanced Controls' }
    ];

    for (const route of routes) {
      await page.goto(route.path);
      await waitForRouteReady(page);

      // Verify no route-level error boundary
      await expect(page.locator('text=Failed to load')).not.toBeVisible();
      await expect(page.locator('text=something went wrong')).not.toBeVisible();

      // Verify page layout title has correct title
      const layoutTitle = page.locator('header h1');
      await expect(layoutTitle).toBeVisible();

      // Debug check: print values to help resolve mismatch if it occurs
      const activeLink = page.locator(`aside a[href="${route.path}"] [aria-current="page"]`);
      const attr = await activeLink.getAttribute('aria-current').catch(() => null);
      if (attr !== 'page') {
        const currentUrl = page.url();
        const html = await page.locator('aside').innerHTML().catch(() => 'no aside');
        console.log(`MISMATCH DEBUG | Path: ${route.path} | Actual URL: ${currentUrl} | attr: ${attr} | HTML excerpt: ${html.slice(0, 1000)}`);
      }

      await expect(activeLink).toHaveAttribute('aria-current', 'page');
    }

    // Verify /dashboard/ai redirect to config
    await page.goto('/dashboard/ai');
    await page.waitForURL('**/dashboard/config');
    const layoutTitle = page.locator('header h1');
    await expect(layoutTitle).toHaveText('Business Setup');
  });

  test('Mobile view E2E (Navigation drawer & Overflow)', async ({ page }) => {
    // Force mobile viewport in this test block if run in desktop project context
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto('/dashboard');
    await waitForRouteReady(page);

    // Verify mobile bottom nav visible
    const bottomNav = page.locator('nav.md\\:hidden, div.md\\:hidden >> text=More');
    await expect(bottomNav.first()).toBeVisible();

    // Click "More" button to open drawer
    await bottomNav.first().click();
    await page.waitForTimeout(500);

    // Verify mobile drawer navigation is open - check visible sidebar
    const drawerNav = page.locator('aside:visible nav').first();
    await expect(drawerNav).toBeVisible();

    // Expand "AI Setup" group if collapsed
    const aiSetupGroup = page.locator('aside:visible button:has-text("AI Setup")');
    if (await aiSetupGroup.count() > 0 && await aiSetupGroup.getAttribute('aria-expanded') !== 'true') {
      await aiSetupGroup.click();
      await page.waitForTimeout(300);
    }

    // Click "AI Knowledge" in mobile drawer and verify drawer closes on navigation
    await page.locator('aside:visible a[href="/dashboard/knowledge"]').click();
    await page.waitForURL('**/dashboard/knowledge');

    // Verify drawer closed
    await expect(drawerNav).not.toBeVisible();

    // Check no horizontal scroll on key mobile pages
    const keyMobilePages = [
      '/dashboard',
      '/dashboard/conversations',
      '/dashboard/bookings',
      '/dashboard/reviews',
      '/dashboard/knowledge',
      '/dashboard/reminders',
      '/dashboard/catalog',
      '/dashboard/leads',
      '/dashboard/config',
      '/dashboard/awa-chat'
    ];

    for (const pagePath of keyMobilePages) {
      await page.goto(pagePath);
      await waitForRouteReady(page);

      // Assert body scrollWidth <= clientWidth to ensure no horizontal overflow
      const overflowInfo = await page.evaluate(() => {
        const body = document.body;
        const main = document.querySelector('#main-content');
        return {
          bodyScroll: body.scrollWidth,
          bodyClient: body.clientWidth,
          mainScroll: main ? main.scrollWidth : 0,
          mainClient: main ? main.clientWidth : 0
        };
      });

      expect(overflowInfo.bodyScroll).toBeLessThanOrEqual(overflowInfo.bodyClient + 2); // 2px margin for browser rounding
      if (overflowInfo.mainScroll > 0) {
        expect(overflowInfo.mainScroll).toBeLessThanOrEqual(overflowInfo.mainClient + 2);
      }
    }
  });

  test('Action Safety E2E (Dialog cancellations)', async ({ page }) => {
    // Test 1: Tools disable confirmation cancel
    await page.goto('/dashboard/tools');
    await waitForRouteReady(page);

    // Get initial state of a critical toggle switch
    const bookingToggle = page.locator('button[aria-label="Toggle Bookings workflow"]');
    await expect(bookingToggle).toBeVisible();
    const isCheckedBefore = await bookingToggle.getAttribute('aria-checked') === 'true';

    // Click the toggle to open confirmation dialog
    await bookingToggle.click();

    // Verify dialog opens
    const dialog = page.locator('div[role="dialog"]');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('button:has-text("Disable workflow")')).toBeVisible();

    // Click cancel button
    await dialog.locator('button:has-text("Cancel")').click();
    await expect(dialog).not.toBeVisible();

    // Verify toggle state remains unchanged
    const isCheckedAfter = await bookingToggle.getAttribute('aria-checked') === 'true';
    expect(isCheckedAfter).toBe(isCheckedBefore);

    // Test 2: Knowledge delete confirmation cancel
    await page.goto('/dashboard/knowledge');
    await waitForRouteReady(page);

    // Open editor by clicking edit button on the first entry
    const editBtn = page.locator('button[aria-label^="Edit "]:visible').first();
    await expect(editBtn).toBeVisible();
    await editBtn.click();

    // Click delete button inside the editor
    const deleteBtn = page.locator('button[aria-label^="Delete "]:visible').first();
    await expect(deleteBtn).toBeVisible();
    await deleteBtn.click();

    // Verify dialog opens
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('button:has-text("Delete permanently")')).toBeVisible();

    // Cancel deletion
    await dialog.locator('button:has-text("Cancel")').click();
    await expect(dialog).not.toBeVisible();

    // Verify entry still exists in table/list
    await expect(page.locator('text=Dental Cleaning FAQ').filter({ visible: true }).first()).toBeVisible();
  });

  test('Form Interaction E2E (Search clear and Config dirty/discard)', async ({ page }) => {
    // Test 1: Knowledge search and clear
    await page.goto('/dashboard/knowledge');
    await waitForRouteReady(page);

    const searchInput = page.locator('input[placeholder*="Search"]');
    await expect(searchInput).toBeVisible();
    await searchInput.fill('Orthodontic');
    await page.waitForTimeout(200);

    // Verify search value is filled
    expect(await searchInput.inputValue()).toBe('Orthodontic');

    // Click the clear button (X)
    const clearBtn = page.locator('button[aria-label="Clear search"]');
    await expect(clearBtn).toBeVisible();
    await clearBtn.click();

    // Verify search input is cleared
    expect(await searchInput.inputValue()).toBe('');

    // Test 2: Config dirty state and discard
    await page.goto('/dashboard/config');
    await waitForRouteReady(page);

    const businessNameInput = page.locator('input[name="business_name"]');
    await expect(businessNameInput).toBeVisible();
    const originalName = await businessNameInput.inputValue();

    // Make edits to business name - click first to trigger pointerdown and type sequentially
    await businessNameInput.click();
    await businessNameInput.pressSequentially('Dental Care Specialists');
    await page.waitForTimeout(200);

    // Sticky actions banner should appear
    const stickyMessage = page.getByText('You have unsaved changes in your configuration');
    await expect(stickyMessage).toBeVisible();

    // Click Discard
    await page.locator('button:has-text("Discard")').first().click();

    // Verify input value resets
    expect(await businessNameInput.inputValue()).toBe(originalName);

    // Sticky banner disappears
    await expect(stickyMessage).not.toBeVisible();
  });

  test('Accessibility smoke check', async ({ page }) => {
    await page.goto('/dashboard');
    await waitForRouteReady(page);

    // 1. Skip link exists and is focusable
    const skipLink = page.locator('a[href="#main-content"]');
    await expect(skipLink).toBeAttached();
    await page.keyboard.press('Tab');
    await expect(skipLink).toBeFocused();

    // 2. Active nav item has aria-current="page"
    const activeLink = page.locator('aside a[href="/dashboard"] [aria-current="page"]');
    await expect(activeLink).toHaveAttribute('aria-current', 'page');

    // Check if mobile or desktop view to handle drawer for aria-expanded checks
    const isMobileView = await page.evaluate(() => window.innerWidth < 768);
    if (isMobileView) {
      const bottomNav = page.locator('nav.md\\:hidden, div.md\\:hidden >> text=More');
      await bottomNav.first().click();
      await page.waitForTimeout(500);
    }

    // 3. Sidebar groups expose aria-expanded
    const groupHeader = page.locator('aside:visible button[aria-expanded]').first();
    await expect(groupHeader).toBeVisible();
    const isExpanded = await groupHeader.getAttribute('aria-expanded');
    expect(isExpanded === 'true' || isExpanded === 'false').toBe(true);

    // 4. Keyboard can focus group headers
    await groupHeader.focus();
    await expect(groupHeader).toBeFocused();

    if (isMobileView) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
    }
  });

  test('Capture visual regression screenshots', async ({ page }, testInfo) => {
    // Desktop screenshot overview
    await page.goto('/dashboard');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'desktop_overview.png');

    // Desktop bookings
    await page.goto('/dashboard/bookings');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'desktop_bookings.png');

    // Desktop reviews
    await page.goto('/dashboard/reviews');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'desktop_reviews.png');

    // Desktop knowledge
    await page.goto('/dashboard/knowledge');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'desktop_knowledge.png');

    // Desktop reminders
    await page.goto('/dashboard/reminders');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'desktop_reminders.png');

    // Desktop AWA Chat invalid color error state
    await page.goto('/dashboard/awa-chat');
    await waitForRouteReady(page);
    await page.locator('#theme-primary').fill('#123');
    await page.locator('text=Use valid hex like #4f46e5').waitFor({ state: 'visible' });
    await captureScreenshot(page, testInfo, 'desktop_awa_chat_validation_error.png');

    // Desktop Tools disable confirmation dialog
    await page.goto('/dashboard/tools');
    await waitForRouteReady(page);
    await page.locator('button[aria-label="Toggle Bookings workflow"]').click();
    await page.locator('div[role="dialog"]').waitFor({ state: 'visible' });
    await captureScreenshot(page, testInfo, 'desktop_tools_disable_dialog.png');
    await page.keyboard.press('Escape');

    // Change to mobile viewport size for mobile screenshots
    await page.setViewportSize({ width: 390, height: 844 });

    // Mobile overview bottom nav
    await page.goto('/dashboard');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'mobile_overview_bottom_nav.png');

    // Mobile More drawer open
    const bottomNav = page.locator('nav.md\\:hidden, div.md\\:hidden >> text=More').first();
    await bottomNav.click();
    await page.waitForTimeout(500);
    await captureScreenshot(page, testInfo, 'mobile_more_drawer_open.png');
    await page.keyboard.press('Escape');

    // Mobile bookings
    await page.goto('/dashboard/bookings');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'mobile_bookings.png');

    // Mobile reviews
    await page.goto('/dashboard/reviews');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'mobile_reviews.png');

    // Mobile knowledge
    await page.goto('/dashboard/knowledge');
    await waitForRouteReady(page);
    await captureScreenshot(page, testInfo, 'mobile_knowledge.png');
  });

});

type ProcessingStatus = 'completed' | 'failed' | 'terminal_failed';

async function setupPublicChatProcessingScenario(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('awa_public_chat_processing-test', JSON.stringify({
      id: 'session-processing-test',
      session_token: 'session-token',
      tenant_name: 'Test Clinic',
      display_name: 'Test Clinic',
      conversation_id: 'conversation-processing-test',
    }));
  });

  let releaseSse: ((message: Record<string, unknown>) => void) | null = null;
  await page.route('**/api/v1/public-chat/sessions/session-processing-test/events?token=*', async (route) => {
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

  await page.route('**/api/v1/public-chat/links/processing-test', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse({
        slug: 'processing-test',
        display_name: 'Test Clinic',
        tenant_name: 'Test Clinic',
        status: 'active',
        privacy_note: 'Test privacy note',
        suggested_replies: [],
        captcha_required: false,
        captcha_mode: 'disabled',
        customer_otp_required: true,
      }),
    });
  });
  const messages: Array<Record<string, unknown>> = [{
    id: 'verification-required',
    direction: 'outbound',
    message_text: 'Please verify before confirming.',
    raw_payload: {
      required_next_step: 'customer_verification',
      verification_action: 'booking_commitment',
    },
    created_at: '2026-07-14T01:00:00.000Z',
  }];
  await page.route('**/api/v1/public-chat/sessions/session-processing-test/messages/list', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: wrapResponse(messages),
    });
  });

  await page.goto('/chat/processing-test');
  await expect(page.getByText('Verify phone to confirm')).toBeVisible();
  await expect(page.getByLabel('Type your message')).toBeDisabled();
  await expect.poll(() => Boolean(releaseSse)).toBe(true);
  return {
    messages,
    hasSseReceiver: () => Boolean(releaseSse),
    sendSse: (message: Record<string, unknown>) => {
      const receiver = releaseSse;
      releaseSse = null;
      receiver?.(message);
    },
  };
}

async function emitProcessingResult(
  page: Page,
  scenario: Awaited<ReturnType<typeof setupPublicChatProcessingScenario>>,
  status: ProcessingStatus,
  createdAt: string,
  recoverable = false,
) {
  scenario.messages.push({
    id: `${status}-${createdAt}`,
    direction: 'outbound',
    message_text: 'Bounded processing result',
    raw_payload: { processing_status: status, recoverable },
    created_at: createdAt,
  });
  await expect.poll(() => scenario.hasSseReceiver(), { timeout: 8000 }).toBe(true);
  scenario.sendSse({
    event_type: 'message',
    message_id: `${status}-${createdAt}`,
    role: 'assistant',
    content: 'Bounded processing result',
    created_at: createdAt,
    raw_payload: { processing_status: status, recoverable },
  });
}

test('public chat successful processing closes verification and clears the processing lock', async ({ page }) => {
  const scenario = await setupPublicChatProcessingScenario(page);
  await emitProcessingResult(page, scenario, 'completed', '2026-07-14T01:01:00.000Z');
  await expect(page.getByText('Verify phone to confirm')).toBeHidden();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});

test('public chat recoverable processing failure unlocks a fresh confirmation path without retrying OTP', async ({ page }) => {
  let otpRequests = 0;
  await page.route('**/api/v1/public-chat/sessions/*/otp/**', async (route) => {
    otpRequests += 1;
    await route.abort();
  });
  const scenario = await setupPublicChatProcessingScenario(page);
  await emitProcessingResult(page, scenario, 'failed', '2026-07-14T01:02:00.000Z', true);
  await expect(page.getByText('Verify phone to confirm')).toBeHidden();
  await expect(page.getByText('That request was not completed. You can confirm again, change a detail, or cancel.')).toBeVisible();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
  expect(otpRequests).toBe(0);
});

test('public chat terminal failure closes stale verification without automatic retry', async ({ page }) => {
  const scenario = await setupPublicChatProcessingScenario(page);
  await emitProcessingResult(page, scenario, 'terminal_failed', '2026-07-14T01:03:00.000Z');
  await expect(page.getByText('Verify phone to confirm')).toBeHidden();
  await expect(page.getByText('That verification or request can no longer be used. Please change or cancel it, or start a fresh confirmation.')).toBeVisible();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});

test('public chat polling race ignores an older processing result', async ({ page }) => {
  const scenario = await setupPublicChatProcessingScenario(page);
  await emitProcessingResult(page, scenario, 'completed', '2026-07-14T01:04:00.000Z');
  await expect(page.getByLabel('Type your message')).toBeEnabled();
  await emitProcessingResult(page, scenario, 'failed', '2026-07-14T01:02:00.000Z', true);
  await expect(page.getByText('That request was not completed. You can confirm again, change a detail, or cancel.')).toBeHidden();
  await expect(page.getByLabel('Type your message')).toBeEnabled();
});
