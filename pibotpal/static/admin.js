const $ = id => document.getElementById(id);
const ROBOT_TOOLS = [
  ['get_status', 'Read robot status', 'Telemetry only'],
  ['list_examples', 'List testing examples', 'Catalog only'],
  ['move', 'Run a movement', 'Requires manual arming'],
  ['stop', 'Stop the robot', 'Disarms and mutes audio'],
  ['set_leg', 'Place a foot', 'Requires manual arming'],
  ['set_servo', 'Set a servo angle', 'Requires manual arming'],
  ['speak', 'Use the speaker', 'Plays speech on the robot'],
];
const markup = `
  <div class="admin-intro"><div><p class="eyebrow">CONNECTIONS & DEFAULTS</p><h2>Give your crawler a model to work with.</h2><p>Connect Ollama, an OpenAI-compatible provider, and the MCP tools you want to make available.</p></div><button id="admin-refresh" class="outline">Refresh settings</button></div>
  <div id="admin-notice" class="notice" role="status" aria-live="polite" hidden></div>
  <div class="admin-grid">
    <article class="panel admin-provider-panel">
      <div class="panel-heading"><h2>Language models</h2><button id="provider-new" class="subtle">+ Add provider</button></div>
      <div id="provider-list" class="admin-list"></div>
      <form id="provider-form" class="admin-form" autocomplete="off">
        <div class="admin-form-title"><h3 id="provider-editor-title">Add a provider</h3><span class="tag" id="provider-secret-status">NO KEY SAVED</span></div>
        <input id="provider-id" type="hidden">
        <div class="form-grid"><label>Name<input id="provider-name" required maxlength="80" placeholder="My Ollama server"></label><label>API type<select id="provider-kind"><option value="ollama">Ollama</option><option value="openai">OpenAI-compatible</option></select></label></div>
        <label>Base URL<input id="provider-url" type="url" required placeholder="http://your-server:11434" maxlength="2048"></label>
        <p id="provider-url-help" class="caption">Use the Ollama server address. Model requests are sent from this Pi.</p>
        <div class="input-action admin-model"><label>Model<input id="provider-model" list="provider-models" placeholder="Enter a model tag, or discover installed models" maxlength="200"><datalist id="provider-models"></datalist></label><button id="provider-discover" type="button" disabled>Discover models</button></div>
        <div id="provider-discovery" class="admin-inline" role="status" hidden></div>
        <label>API key <span class="optional">(optional for local servers)</span><input id="provider-key" type="password" autocomplete="new-password" maxlength="8192" placeholder="Only needed if your provider requires a key"></label>
        <div class="form-flags"><label class="check-label"><input id="provider-enabled" type="checkbox" checked>Enabled</label><label class="check-label"><input id="provider-clear-key" type="checkbox">Remove saved key</label></div>
        <p class="caption">Keys stay on the Pi. Leave this field blank to keep a saved key; changing its destination requires a new key or removing the old one.</p>
        <div class="admin-actions"><button type="submit" class="primary">Save provider</button><button id="provider-test" type="button" disabled>Test prompt ↗</button><button id="provider-delete" type="button" class="delete-button" hidden>Delete provider</button></div>
      </form>
    </article>
    <article class="panel admin-defaults-panel">
      <div class="panel-heading"><h2>Model defaults</h2><span class="tag">SHARED WITH MCP</span></div>
      <form id="defaults-form" class="admin-form">
        <div class="form-grid"><label>Testing provider<select id="default-test-provider"><option value="">Not configured</option></select></label><label>Assistant provider<select id="default-assistant-provider"><option value="">Not configured</option></select></label></div>
        <label>System instructions<textarea id="default-system-prompt" rows="4" maxlength="8000" placeholder="How should the model help with the crawler?"></textarea></label>
        <div class="form-grid three"><label>Max output tokens<input id="default-max-tokens" type="number" min="32" max="8192" step="1" value="1024" required></label><label>Temperature<input id="default-temperature" type="number" min="0" max="2" step="0.1" value="0.2" required></label><label>Timeout (seconds)<input id="default-timeout" type="number" min="10" max="180" step="1" value="120" required></label></div>
        <fieldset class="tool-fieldset"><legend>Built-in tools available to the model</legend><div id="robot-tool-choices" class="tool-choices"></div></fieldset>
        <p class="caption">These permissions apply when a prompt enables MCP tools. The model cannot arm the robot or enable the microphone. Movement still requires you to arm it.</p>
        <div class="admin-actions"><button type="submit" class="primary">Save defaults</button></div>
      </form>
    </article>
  </div>
  <article class="panel admin-mcp-panel">
    <div class="panel-heading"><h2>Remote MCP connections</h2><button id="connection-new" class="subtle">+ Add connection</button></div>
    <div class="admin-mcp-layout"><div class="admin-mcp-editor"><div id="mcp-connection-list" class="admin-list"></div>
      <form id="mcp-connection-form" class="admin-form" autocomplete="off">
        <div class="admin-form-title"><h3 id="connection-editor-title">Add an MCP server</h3><span id="connection-secret-status" class="tag">NO TOKEN SAVED</span></div>
        <input id="mcp-connection-id" type="hidden">
        <div class="form-grid"><label>Name<input id="mcp-connection-name" required maxlength="80" placeholder="Home tools"></label><label>Transport<select id="mcp-connection-transport"><option value="streamable_http">Streamable HTTP</option><option value="sse">Legacy SSE</option></select></label></div>
        <label>Server URL<input id="mcp-connection-url" type="url" required maxlength="2048" placeholder="http://your-server:8000/mcp"></label>
        <label>Bearer token <span class="optional">(if required)</span><input id="mcp-connection-token" type="password" autocomplete="new-password" maxlength="8192" placeholder="Leave blank to keep a saved token"></label>
        <div class="form-flags"><label class="check-label"><input id="mcp-connection-enabled" type="checkbox" checked>Enabled</label><label class="check-label"><input id="mcp-connection-clear-token" type="checkbox">Remove saved token</label></div>
        <p class="caption">Saving a connection does not run its tools. Inspect the server, select permitted tools, then save again.</p>
        <div class="admin-actions"><button type="submit" class="primary">Save connection</button><button id="connection-inspect" type="button" disabled>Inspect tools</button><button id="connection-delete" type="button" class="delete-button" hidden>Delete</button></div>
      </form></div>
      <div class="admin-tool-inspector"><div class="admin-form-title"><div><h3>Permitted tools</h3><p class="caption" id="connection-inspection-summary">Save a connection, then inspect its tools.</p></div><span class="tag" id="allowed-tools-count">0 SELECTED</span></div><div id="remote-tool-list" class="remote-tool-list"></div><p class="caption">Only selected tools can be called by the model. Tool descriptions and results come from that server.</p></div>
    </div>
  </article>
  <div class="admin-grid admin-bottom-grid">
    <article class="panel admin-playground">
      <div class="panel-heading"><h2>Try a model</h2><span id="llm-job-status" class="tag">READY</span></div>
      <form id="llm-test-form" class="admin-form">
        <div class="form-grid"><label>Provider<select id="llm-provider"><option value="">Use configured default</option></select></label><label>Default to use<select id="llm-purpose"><option value="tests">Testing</option><option value="assistant">Assistant / MCP</option></select></label></div>
        <label>Prompt<textarea id="llm-prompt" rows="4" required maxlength="12000" placeholder="Describe how you can help monitor this crawler."></textarea></label>
        <label class="check-label"><input id="llm-use-tools" type="checkbox">Allow configured MCP tools for this prompt</label>
        <p class="caption">Sends this text to the selected provider. Camera and microphone data are not included.</p>
        <div class="admin-actions"><button id="llm-run" type="submit" class="primary">Run prompt ↗</button><button id="llm-cancel" type="button" disabled>Cancel</button></div>
      </form>
      <div class="llm-result"><p class="eyebrow">MODEL RESPONSE</p><div id="llm-response" role="status" aria-live="polite">Choose a provider and send a test prompt.</div><details id="llm-trace"><summary>Tool activity & response details</summary><pre id="llm-details">No run yet.</pre></details></div>
    </article>
    <article class="panel admin-server-panel">
      <div class="panel-heading"><h2>Connect to PiBotPal</h2><span class="tag teal">MCP SERVER</span></div>
      <div class="admin-form"><p class="caption">Use this endpoint from an MCP client on your tailnet. It exposes the same controls, model jobs, and administration tools as this dashboard.</p><label>Server endpoint<input id="local-mcp-url" readonly aria-label="PiBotPal MCP endpoint"></label><div class="admin-actions"><button id="copy-mcp-url" type="button">Copy URL</button><button id="copy-mcp-config" type="button">Copy configuration</button></div><pre id="local-mcp-config" class="admin-code"></pre><p class="caption" id="local-mcp-auth">Access follows this Pi’s Tailscale rules. Admin currently uses the same trusted access as robot control; there is no separate admin login.</p><div class="admin-facts"><span>Credentials</span><strong>Stored on this Pi</strong><span>Model downloads</span><strong>Managed on your model server</strong><span>Hardware ownership</span><strong>One shared controller</strong></div></div>
    </article>
  </div>`;

export function initAdmin({call, notice, onConfigChanged, onSectionChange}) {
  $('admin').innerHTML = markup;
  const intro=$('admin').querySelector('.admin-intro');
  intro.querySelector('.eyebrow').textContent='ROBOT SETTINGS';
  intro.querySelector('h2').textContent='Everything in its place.';
  intro.querySelector('p:last-child').textContent='Manage hardware, cameras, model providers, and connections from separate sections.';
  const sections=[['hardware','Hardware'],['cameras','Cameras'],['models','Models'],['mcp','MCP connections'],['playground','Playground'],['system','System']];
  const navigation=document.createElement('div');navigation.className='admin-subtabs';navigation.setAttribute('role','tablist');navigation.setAttribute('aria-label','Admin sections');
  const panels=new Map();
  for(const [id,title] of sections){
    const button=document.createElement('button');button.type='button';button.id='admin-tab-'+id;button.textContent=title;button.dataset.adminSection=id;button.setAttribute('role','tab');button.setAttribute('aria-controls','admin-'+id);
    const panel=document.createElement('section');panel.id='admin-'+id;panel.className='admin-subpanel';panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.hidden=true;
    navigation.append(button);panels.set(id,panel);
  }
  intro.after(navigation);
  const modelGrid=$('admin').querySelector('.admin-grid');panels.get('models').append(modelGrid);
  panels.get('mcp').append($('admin').querySelector('.admin-mcp-panel'));
  const bottom=$('admin').querySelector('.admin-bottom-grid');
  panels.get('playground').append(bottom.querySelector('.admin-playground'));
  panels.get('system').append(bottom.querySelector('.admin-server-panel'));bottom.remove();
  for(const panel of panels.values())$('admin').append(panel);
  let activeSection='hardware';
  function selectSection(name='hardware',updateHash=true){
    if(!panels.has(name))name='hardware';activeSection=name;
    for(const button of navigation.querySelectorAll('[role="tab"]')){const selected=button.dataset.adminSection===name;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;button.classList.toggle('active',selected);}
    for(const [id,panel] of panels)panel.hidden=id!==name;
    if(updateHash)history.replaceState(null,'','#admin/'+name);
    onSectionChange?.(name);
  }
  navigation.addEventListener('click',event=>{const button=event.target.closest('[data-admin-section]');if(button)selectSection(button.dataset.adminSection);});
  navigation.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const tabs=[...navigation.querySelectorAll('[role="tab"]')];let index=tabs.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();index=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    selectSection(tabs[index].dataset.adminSection);tabs[index].focus();
  });
  selectSection('hardware',false);
  let state = {providers: [], connections: [], defaults: {}};
  let connectionTools = [];
  let allowedTools = new Set();
  let jobId = null;
  let pollTimer = null;
  let loading = false;
  let loaded = false;
  const message = (text, kind = '') => {
    $('admin-notice').textContent = text;
    $('admin-notice').className = `notice ${kind}`;
    $('admin-notice').hidden = !text;
  };
  const rpc = (name, args = {}) => call(name, args, {timeout: 35000});
  const findProvider = id => state.providers.find(p => p.id === id);
  const findConnection = id => state.connections.find(c => c.id === id);
  async function busy(button, fn) {
    if (!button) throw new Error('Form submit button unavailable');
    const old = button.textContent;
    button.disabled = true;
    button.textContent = 'Working…';
    try { await fn(); }
    catch (error) { message(error.message, 'error'); }
    finally { button.textContent = old; button.disabled = false; }
  }
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function option(value, label) {
    const element = node('option', '', label); element.value = value; return element;
  }
  function providerOptions(select, empty, selected) {
    select.replaceChildren(option('', empty), ...state.providers.filter(p => p.enabled).map(p => option(p.id, `${p.name}${p.model ? ` · ${p.model}` : ' · model not selected'}`)));
    select.value = selected || '';
  }
  function renderLists() {
    for (const [containerId, entries, edit, subtitle] of [
      ['provider-list', state.providers, editProvider, p => `${p.kind === 'ollama' ? 'Ollama' : 'OpenAI-compatible'} · ${p.model || 'Select a model'}`],
      ['mcp-connection-list', state.connections, editConnection, c => c.url],
    ]) {
      const container = $(containerId);
      container.replaceChildren();
      if (!entries.length) container.append(node('p', 'admin-empty', containerId === 'provider-list' ? 'No providers yet. Add your Ollama server below.' : 'No remote MCP servers connected.'));
      for (const entry of entries) {
        const button = node('button', 'admin-saved-item'); button.type = 'button';
        const text = node('span', 'admin-saved-info'); text.append(node('strong', '', entry.name), node('small', '', subtitle(entry)));
        button.append(text, node('span', `tag ${entry.enabled ? 'teal' : ''}`, entry.enabled ? 'ENABLED' : 'DISABLED'));
        button.addEventListener('click', () => edit(entry));
        container.append(button);
      }
    }
    const oldTest = $('default-test-provider').value, oldAssistant = $('default-assistant-provider').value, oldLlm = $('llm-provider').value;
    providerOptions($('default-test-provider'), 'Not configured', oldTest);
    providerOptions($('default-assistant-provider'), 'Not configured', oldAssistant);
    providerOptions($('llm-provider'), 'Use configured default', oldLlm);
  }
  function renderDefaults() {
    const d = state.defaults || {};
    $('default-test-provider').value = d.test_provider_id || '';
    $('default-assistant-provider').value = d.assistant_provider_id || '';
    $('default-system-prompt').value = d.system_prompt || '';
    $('default-max-tokens').value = d.max_tokens ?? 1024;
    $('default-temperature').value = d.temperature ?? .2;
    $('default-timeout').value = d.timeout_seconds ?? 120;
    $('robot-tool-choices').replaceChildren();
    for (const [id, title, hint] of ROBOT_TOOLS) {
      const label = node('label', 'tool-choice');
      const input = node('input'); input.type = 'checkbox'; input.value = id; input.checked = (d.robot_tools || []).includes(id);
      const text = node('span'); text.append(node('strong', '', title), node('small', '', hint));
      label.append(input, text); $('robot-tool-choices').append(label);
    }
  }
  function renderServer() {
    const url = state.server?.mcp_url || `${location.origin}/mcp`;
    $('local-mcp-url').value = url;
    $('local-mcp-config').textContent = JSON.stringify({mcpServers: {pibotpal: {url}}}, null, 2);
  }
  async function refresh({resetDefaults = true} = {}) {
    if (loading) return;
    loading = true;
    try {
      state = await rpc('admin_get');
      state.providers ||= []; state.connections ||= [];
      renderLists(); if (resetDefaults) renderDefaults(); renderServer(); loaded = true;
    } finally { loading = false; }
  }
  async function changed() {
    await refresh();
    await onConfigChanged?.();
  }
  function providerHelp() {
    const ollama = $('provider-kind').value === 'ollama';
    $('provider-url').placeholder = ollama ? 'http://your-server:11434' : 'https://api.example.com/v1';
    $('provider-url-help').textContent = ollama ? 'Use the Ollama server address. Model requests are sent from this Pi.' : 'Use the API base URL including /v1 when required by your provider.';
  }
  function editProvider(provider = null) {
    $('provider-form').reset();
    $('provider-id').value = provider?.id || '';
    $('provider-name').value = provider?.name || '';
    $('provider-kind').value = provider?.kind || 'ollama';
    $('provider-url').value = provider?.base_url || '';
    $('provider-model').value = provider?.model || '';
    $('provider-enabled').checked = provider?.enabled !== false;
    $('provider-editor-title').textContent = provider ? 'Edit provider' : 'Add a provider';
    $('provider-secret-status').textContent = provider?.api_key_set ? 'KEY SAVED' : 'NO KEY SAVED';
    $('provider-key').placeholder = provider?.api_key_set ? 'Saved key · leave blank to keep' : 'Only needed if your provider requires a key';
    $('provider-discover').disabled = !provider;
    $('provider-test').disabled = !provider || !provider.enabled || !provider.model;
    $('provider-delete').hidden = !provider;
    $('provider-models').replaceChildren(); $('provider-discovery').hidden = true;
    providerHelp();
  }
  function providerPayload() {
    const result = {name: $('provider-name').value.trim(), kind: $('provider-kind').value, base_url: $('provider-url').value.trim(), model: $('provider-model').value.trim(), enabled: $('provider-enabled').checked, api_key: $('provider-key').value, clear_api_key: $('provider-clear-key').checked};
    if ($('provider-id').value) result.id = $('provider-id').value;
    return result;
  }
  function editConnection(connection = null) {
    $('mcp-connection-form').reset();
    $('mcp-connection-id').value = connection?.id || '';
    $('mcp-connection-name').value = connection?.name || '';
    $('mcp-connection-url').value = connection?.url || '';
    $('mcp-connection-transport').value = connection?.transport || 'streamable_http';
    $('mcp-connection-enabled').checked = connection?.enabled !== false;
    $('connection-editor-title').textContent = connection ? 'Edit MCP server' : 'Add an MCP server';
    $('connection-secret-status').textContent = connection?.token_set ? 'TOKEN SAVED' : 'NO TOKEN SAVED';
    $('connection-inspect').disabled = !connection;
    $('connection-delete').hidden = !connection;
    allowedTools = new Set(connection?.allowed_tools || []);
    connectionTools = [];
    $('connection-inspection-summary').textContent = connection ? 'Inspect this saved server to discover its tools.' : 'Save a connection, then inspect its tools.';
    renderRemoteTools();
  }
  function connectionPayload() {
    const result = {name: $('mcp-connection-name').value.trim(), url: $('mcp-connection-url').value.trim(), transport: $('mcp-connection-transport').value, enabled: $('mcp-connection-enabled').checked, bearer_token: $('mcp-connection-token').value, clear_bearer_token: $('mcp-connection-clear-token').checked, allowed_tools: [...allowedTools]};
    if ($('mcp-connection-id').value) result.id = $('mcp-connection-id').value;
    return result;
  }
  function renderRemoteTools() {
    const container = $('remote-tool-list'); container.replaceChildren();
    const all = new Map(connectionTools.map(t => [t.name, t]));
    for (const name of allowedTools) if (!all.has(name)) all.set(name, {name, description: 'Saved permission. Inspect the server to check availability.'});
    if (!all.size) container.append(node('p', 'admin-empty', 'No tools selected. New connections grant no tool permissions.'));
    for (const tool of all.values()) {
      const label = node('label', 'remote-tool');
      const input = node('input'); input.type = 'checkbox'; input.value = tool.name; input.checked = allowedTools.has(tool.name);
      const text = node('span'); text.append(node('strong', '', tool.name), node('small', '', tool.description || 'No description supplied.'));
      input.addEventListener('change', () => {input.checked ? allowedTools.add(tool.name) : allowedTools.delete(tool.name); $('allowed-tools-count').textContent = `${allowedTools.size} SELECTED`;});
      label.append(input, text); container.append(label);
    }
    $('allowed-tools-count').textContent = `${allowedTools.size} SELECTED`;
  }
  function showJob(job) {
    const status = job.status || 'unknown';
    $('llm-job-status').textContent = status.toUpperCase();
    $('llm-job-status').className = `tag ${status === 'running' || status === 'queued' ? 'orange' : status === 'failed' ? 'red' : 'teal'}`;
    const running = ['running', 'queued', 'pending'].includes(status);
    $('llm-run').disabled = running;
    $('llm-cancel').disabled = !running;
    const result = job.result;
    if (job.error) $('llm-response').textContent = job.error;
    else if (result) $('llm-response').textContent = typeof result === 'string' ? result : result.text ?? result.content ?? result.response ?? result.message?.content ?? JSON.stringify(result, null, 2);
    else $('llm-response').textContent = running ? 'Waiting for the model… Loading a model can take a little longer.' : status === 'cancelled' ? 'Prompt cancelled.' : 'The model returned no text.';
    $('llm-details').textContent = JSON.stringify({id: job.id || jobId, status, trace: job.trace || [], result: result || null}, null, 2);
    return running;
  }
  async function pollJob() {
    if (!jobId) return;
    const requested = jobId;
    try {
      const job = await rpc('llm_job', {job_id: requested});
      if (jobId !== requested) return;
      if (showJob(job)) pollTimer = setTimeout(pollJob, 1000);
      else pollTimer = null;
    } catch (error) {
      message(`Could not read model job: ${error.message}`, 'error');
      // A transient reconnect must not abandon the cancellation control.
      if (jobId === requested) pollTimer = setTimeout(pollJob, 3000);
    }
  }
  $('provider-new').addEventListener('click', () => editProvider());
  $('provider-kind').addEventListener('change', providerHelp);
  $('provider-form').addEventListener('submit', event => {
    event.preventDefault();
    busy(event.submitter, async () => {
      const saved = await rpc('admin_save_provider', {provider: providerPayload()});
      $('provider-key').value = '';
      await changed(); editProvider(findProvider(saved.id) || saved);
      message('Provider saved. Discover models or run a test prompt.');
    });
  });
  $('provider-discover').addEventListener('click', event => busy(event.currentTarget, async () => {
    const id = $('provider-id').value;
    if (!id) throw new Error('Save the provider before discovering models.');
    const result = await rpc('admin_discover_models', {provider_id: id});
    if ($('provider-id').value !== id) return;
    const models = result.models || [];
    $('provider-models').replaceChildren(...models.map(model => option(typeof model === 'string' ? model : model.id || model.name, typeof model === 'string' ? model : model.id || model.name)));
    const display = $('provider-discovery'); display.replaceChildren(); display.hidden = false;
    display.append(node('p', '', models.length ? `${models.length} models found. Select a tag, then save the provider.` : 'No installed models returned. Load a model on your server, then try again.'));
    const select = node('select'); select.setAttribute('aria-label', 'Discovered models');
    select.append(option('', 'Choose a discovered model'), ...models.map(model => option(typeof model === 'string' ? model : model.id || model.name, typeof model === 'string' ? model : model.id || model.name)));
    select.addEventListener('change', () => {if (select.value) $('provider-model').value = select.value;});
    if (models.length) display.append(select);
    message('Provider reached successfully.');
  }));
  $('provider-delete').addEventListener('click', event => busy(event.currentTarget, async () => {
    await rpc('admin_delete_provider', {provider_id: $('provider-id').value});
    await changed(); editProvider(); message('Provider deleted. Defaults that used it have been cleared.');
  }));
  $('provider-test').addEventListener('click', () => {
    selectSection('playground');
    $('llm-provider').value = $('provider-id').value;
    if (!$('llm-prompt').value) $('llm-prompt').value = 'Reply with a short greeting and the model name, if you know it.';
    $('llm-test-form').scrollIntoView({behavior: 'smooth', block: 'center'});
    $('llm-prompt').focus({preventScroll: true});
  });
  $('defaults-form').addEventListener('submit', event => {
    event.preventDefault();
    busy(event.submitter, async () => {
      await rpc('admin_save_defaults', {defaults: {
        test_provider_id: $('default-test-provider').value || null,
        assistant_provider_id: $('default-assistant-provider').value || null,
        system_prompt: $('default-system-prompt').value,
        max_tokens: Number($('default-max-tokens').value), temperature: Number($('default-temperature').value), timeout_seconds: Number($('default-timeout').value),
        robot_tools: [...$('robot-tool-choices').querySelectorAll('input:checked')].map(input => input.value),
      }});
      await changed(); message('Defaults saved for the testing tools and MCP model jobs.');
    });
  });
  $('connection-new').addEventListener('click', () => editConnection());
  $('mcp-connection-form').addEventListener('submit', event => {
    event.preventDefault();
    busy(event.submitter, async () => {
      const saved = await rpc('admin_save_connection', {connection: connectionPayload()});
      $('mcp-connection-token').value = '';
      await changed(); editConnection(findConnection(saved.id) || saved); message('MCP connection and tool permissions saved.');
    });
  });
  $('connection-inspect').addEventListener('click', event => busy(event.currentTarget, async () => {
    const id = $('mcp-connection-id').value;
    const result = await rpc('admin_probe_connection', {connection_id: id});
    if ($('mcp-connection-id').value !== id) return;
    connectionTools = result.tools || [];
    const name = typeof result.server === 'string' ? result.server : result.server?.name || 'MCP server';
    $('connection-inspection-summary').textContent = `${name} · ${connectionTools.length} tools · protocol ${result.protocol_version || 'negotiated'}`;
    renderRemoteTools(); message('Connection verified. Select permitted tools and save the connection.');
  }));
  $('connection-delete').addEventListener('click', event => busy(event.currentTarget, async () => {
    await rpc('admin_delete_connection', {connection_id: $('mcp-connection-id').value});
    await changed(); editConnection(); message('MCP connection deleted.');
  }));
  $('llm-test-form').addEventListener('submit', async event => {
    event.preventDefault();
    clearTimeout(pollTimer); $('llm-run').disabled = true; message('');
    try {
      const job = await rpc('llm_start', {prompt: $('llm-prompt').value.trim(), provider_id: $('llm-provider').value || null, purpose: $('llm-purpose').value, use_tools: $('llm-use-tools').checked});
      jobId = job.id; showJob(job); pollTimer = setTimeout(pollJob, 700);
    } catch (error) { message(error.message, 'error'); $('llm-run').disabled = false; }
  });
  $('llm-cancel').addEventListener('click', event => busy(event.currentTarget, async () => {
    if (!jobId) return;
    const job = await rpc('llm_cancel', {job_id: jobId});
    clearTimeout(pollTimer); showJob(job);
    // The server may acknowledge cancellation before its task completes.
    pollTimer = setTimeout(pollJob, 500);
    message('Model cancellation requested.');
  }).then(() => {$('llm-cancel').disabled = !['RUNNING', 'QUEUED', 'PENDING'].includes($('llm-job-status').textContent);}));
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); message('Copied to clipboard.'); }
    catch { message('Clipboard unavailable. Select and copy the displayed configuration.', 'warning'); }
  }
  $('copy-mcp-url').addEventListener('click', () => copy($('local-mcp-url').value));
  $('copy-mcp-config').addEventListener('click', () => copy($('local-mcp-config').textContent));
  $('admin-refresh').addEventListener('click', event => busy(event.currentTarget, async () => {await refresh(); message('Settings refreshed.');}));
  editProvider(); editConnection(); renderDefaults(); renderServer();
  return {
    selectSection,
    get section(){return activeSection;},
    async refresh() { try { await refresh({resetDefaults: !loaded}); } catch (error) { message(error.message, 'error'); } },
    async cancelActive() { if (jobId) { try { showJob(await rpc('llm_cancel', {job_id: jobId})); } catch {} } },
  };
}
