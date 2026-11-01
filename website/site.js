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
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
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
    if (!reducedMotion.matches) {
      panel.getAnimations().forEach(animation => animation.cancel());
      panel.animate([{opacity:.45,transform:'translateY(9px)'},{opacity:1,transform:'translateY(0)'}], {duration:380,easing:'cubic-bezier(.22,1,.36,1)'});
    }
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

  // Reveal once; no scroll interception and no animation libraries to download.
  if ('IntersectionObserver' in window) {
    const revealItems = document.querySelectorAll('.story-heading, .section-heading, .workflow-steps article, .illustration-copy, .illustration-figure, .connection-box, .details-heading, .detail-grid article, .faq-list, .footer-main');
    const revealObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) { entry.target.classList.add('is-visible'); revealObserver.unobserve(entry.target); }
      });
    }, {threshold:0,rootMargin:'0px 0px -45px 0px'});
    revealItems.forEach(item => {
      item.dataset.reveal = '';
      if (item.matches('article')) item.style.setProperty('--reveal-delay', `${Math.min([...item.parentElement.children].indexOf(item), 2) * 90}ms`);
      revealObserver.observe(item);
    });
    document.documentElement.classList.add('motion-ready');
    const ambientObserver = new IntersectionObserver(entries => entries.forEach(entry => entry.target.classList.toggle('is-visible',entry.isIntersecting)), {threshold:0});
    document.querySelectorAll('.workflow, .hero').forEach(item => ambientObserver.observe(item));
  }

  const steps = [...document.querySelectorAll('[data-story-step]')];
  const frames = [...document.querySelectorAll('[data-story-frame]')];
  const storyButtons = [...document.querySelectorAll('[data-story-target]')];
  const titles = ['Illustrated notes', 'Choose your destination', 'Your task history'];
  const story = document.querySelector('.story-layout');
  const desktopStory = window.matchMedia('(min-width: 701px)');
  let activeStep = -1;
  let manualUntil = 0;
  function showStory(index) {
    if (index === activeStep) return;
    activeStep = index;
    steps.forEach((step,i) => step.classList.toggle('is-active', i === index));
    frames.forEach((frame,i) => {
      const selected = i === index;
      frame.classList.toggle('is-active', selected);
      frame.tabIndex = selected ? 0 : -1;
      frame.setAttribute('aria-hidden', String(!selected));
    });
    storyButtons.forEach((button,i) => button.setAttribute('aria-pressed',String(i === index)));
    document.getElementById('story-stage-title').textContent = titles[index];
  }
  storyButtons.forEach(button => button.addEventListener('click', () => {
    const index = Number(button.dataset.storyTarget);
    manualUntil = performance.now() + 1100;
    showStory(index);
    steps[index].scrollIntoView({block:'center',behavior:reducedMotion.matches ? 'instant' : 'smooth'});
  }));
  showStory(0);
  const progress = document.querySelector('.reading-progress');
  let scrollPending = false;
  function updateScroll() {
    scrollPending = false;
    const maximum = document.documentElement.scrollHeight - innerHeight;
    progress.style.transform = `scaleX(${maximum > 0 ? Math.max(0,Math.min(1,scrollY / maximum)) : 0})`;
    if (!desktopStory.matches || performance.now() < manualUntil) return;
    const bounds = story.getBoundingClientRect();
    if (bounds.bottom < 0 || bounds.top > innerHeight) return;
    const centers = steps.map(step => { const rect = step.getBoundingClientRect(); return Math.abs(rect.top + rect.height / 2 - innerHeight * .53); });
    showStory(centers.indexOf(Math.min(...centers)));
  }
  function scheduleScroll() {
    if (!scrollPending) { scrollPending = true; requestAnimationFrame(updateScroll); }
  }
  window.addEventListener('scroll',scheduleScroll,{passive:true});
  window.addEventListener('resize',scheduleScroll,{passive:true});
  desktopStory.addEventListener('change',scheduleScroll);
  window.addEventListener('load',scheduleScroll,{once:true});
  document.addEventListener('visibilitychange', () => document.documentElement.classList.toggle('page-hidden',document.hidden));
  document.querySelector('.hero-product').addEventListener('animationend',event => {
    if (event.target.matches('.hero-product')) event.target.style.animation = 'none';
  },{once:true});
  scheduleScroll();
})();
