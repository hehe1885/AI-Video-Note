'use strict';
(() => {
  const menu = document.querySelector('.menu-toggle');
  const nav = document.getElementById('site-nav');
  const closeMenu = () => {
    nav.classList.remove('is-open');
    menu.setAttribute('aria-expanded', 'false');
    menu.setAttribute('aria-label', 'Open navigation');
  };
  menu.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    nav.classList.toggle('is-open', open);
    menu.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  });
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') { closeMenu(); menu.focus(); }
  });
  window.matchMedia('(min-width: 801px)').addEventListener('change', event => { if (event.matches) closeMenu(); });

  const methods = {
    api: { kicker: 'DIRECT MODEL CONNECTION', title: 'Your endpoint. Your model.', description: 'Choose a supported service or a custom endpoint, then configure the model and your API key. The app calls the model for transcript review and note generation.', steps: ['Select a provider or custom endpoint', 'Set the model and API key', 'Choose your note settings and start'], note: 'API usage may incur provider charges. A custom endpoint needs its own Base URL.' },
    agent: { kicker: 'AGENT FROM THE APP INTERFACE', title: 'Let your local client handle it.', description: 'Choose Agent mode and the in-app processing option. Select an already configured local client and model, then submit the task from the app interface.', steps: ['Install and configure a supported local Agent client', 'Select the client and model in the app', 'Submit a task and receive the generated notes'], note: 'Uses the client’s configured model and account. Client availability, permissions, and usage limits apply.' },
    skill: { kicker: 'ON-DEMAND CALLS FROM CHAT', title: 'A skill for your Agent.', description: 'Export the included Skill package and install it in a compatible Agent. Ask it to process a video in chat; the Skill can start the local service when needed.', steps: ['Export and install the complete Skill package', 'Refresh your Agent’s skill list', 'Ask the Agent to use AI Video Note for a video'], note: 'Requires a compatible Agent with Python and permission to start the local app and access its service. MCP is optional for this route.' },
    mcp: { kicker: 'A TOOL CONNECTION FOR YOUR CLIENT', title: 'Connect the local service.', description: 'Add the app’s MCP connection to your AI client. The Agent can call the local service to prepare materials, retrieve transcripts, and return the note result.', steps: ['Start AI Video Note on your computer', 'Add its MCP address to your AI client', 'Let the Agent call the app and return the result'], note: 'The service runs on your computer. Skill and MCP are alternative entry points for an Agent, and do not require simultaneous setup.' }
  };
  const tabs = [...document.querySelectorAll('[data-connection]')];
  const panel = document.getElementById('connection-panel');
  function activate(tab) {
    const method = methods[tab.dataset.connection];
    tabs.forEach(item => { const selected = item === tab; item.setAttribute('aria-selected', String(selected)); item.tabIndex = selected ? 0 : -1; });
    panel.setAttribute('aria-labelledby', tab.id);
    document.getElementById('connection-kicker').textContent = method.kicker;
    document.getElementById('connection-title').textContent = method.title;
    document.getElementById('connection-description').textContent = method.description;
    document.getElementById('connection-note').textContent = method.note;
    const list = document.getElementById('connection-steps');
    list.replaceChildren(...method.steps.map(text => { const li = document.createElement('li'); li.textContent = text; return li; }));
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); activate(tabs[next]); tabs[next].focus();
    });
  });
})();
