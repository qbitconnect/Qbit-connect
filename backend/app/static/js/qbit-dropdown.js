/**
 * QBIT CONNECT / GROWTH OS — Enterprise Custom Dropdown Enhancer
 * Modern, accessible, searchable custom dropdown component replacing native browser selects.
 * 
 * Features:
 * - 100% two-way sync with underlying native <select>
 * - Preserves native form submission, FormData, and 'change' events
 * - Dark enterprise styling adhering to Stitch design system
 * - Soft rounded corners (8px radius)
 * - Auto search filter for lists with > 5 options
 * - Portal/Fixed floating menu preventing parent container overflow clipping
 * - Full keyboard navigation (ArrowUp, ArrowDown, Enter, Escape, Tab)
 * - Auto-detects newly inserted <select> elements via MutationObserver
 */

(function () {
  'use strict';

  function initDropdown(selectEl) {
    if (!selectEl || selectEl.dataset.qbitEnhanced === 'true' || selectEl.dataset.native === 'true') {
      return;
    }

    // Mark as enhanced
    selectEl.dataset.qbitEnhanced = 'true';

    // Hide native select visually but keep in DOM for form submission & accessibility
    selectEl.style.position = 'absolute';
    selectEl.style.opacity = '0';
    selectEl.style.pointerEvents = 'none';
    selectEl.style.width = '1px';
    selectEl.style.height = '1px';
    selectEl.style.margin = '-1px';
    selectEl.style.clip = 'rect(0,0,0,0)';
    selectEl.setAttribute('tabindex', '-1');

    // Create wrapper
    var wrapper = document.createElement('div');
    wrapper.className = 'qbit-select-wrapper';
    wrapper.style.position = 'relative';
    wrapper.style.display = selectEl.style.display === 'inline' || selectEl.style.display === 'inline-block' ? 'inline-block' : 'block';
    if (selectEl.classList.contains('w-full') || selectEl.style.width === '100%') {
      wrapper.style.width = '100%';
    } else if (selectEl.style.flex) {
      wrapper.style.flex = selectEl.style.flex;
    }
    if (selectEl.style.minWidth) {
      wrapper.style.minWidth = selectEl.style.minWidth;
    }

    // Insert wrapper before select and move select inside
    selectEl.parentNode.insertBefore(wrapper, selectEl);
    wrapper.appendChild(selectEl);

    // Create trigger button
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'qbit-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    if (selectEl.disabled) {
      trigger.disabled = true;
    }

    var labelSpan = document.createElement('span');
    labelSpan.className = 'qbit-select-label';

    var chevron = document.createElement('span');
    chevron.className = 'material-symbols-outlined qbit-select-chevron';
    chevron.textContent = 'expand_more';

    trigger.appendChild(labelSpan);
    trigger.appendChild(chevron);
    wrapper.appendChild(trigger);

    // Create floating menu
    var menu = document.createElement('div');
    menu.className = 'qbit-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.style.display = 'none';

    // Optional search input
    var searchContainer = null;
    var searchInput = null;
    var optionsCount = selectEl.options.length;

    if (optionsCount > 5) {
      searchContainer = document.createElement('div');
      searchContainer.className = 'qbit-select-search';
      
      var searchIcon = document.createElement('span');
      searchIcon.className = 'material-symbols-outlined search-icon';
      searchIcon.textContent = 'search';

      searchInput = document.createElement('input');
      searchInput.type = 'text';
      searchInput.className = 'qbit-select-search-input';
      searchInput.placeholder = 'Search options...';
      searchInput.autocomplete = 'off';

      searchContainer.appendChild(searchIcon);
      searchContainer.appendChild(searchInput);
      menu.appendChild(searchContainer);
    }

    var optionsList = document.createElement('div');
    optionsList.className = 'qbit-select-options';
    menu.appendChild(optionsList);

    // Append menu to body for zero overflow clipping
    document.body.appendChild(menu);

    function updateTriggerText() {
      var selectedOption = selectEl.options[selectEl.selectedIndex];
      if (selectedOption) {
        labelSpan.textContent = selectedOption.textContent.trim() || 'Select an option';
        if (!selectedOption.value) {
          trigger.classList.add('is-placeholder');
        } else {
          trigger.classList.remove('is-placeholder');
        }
      } else {
        labelSpan.textContent = 'Select an option';
        trigger.classList.add('is-placeholder');
      }
    }

    function renderOptions(filterText) {
      optionsList.innerHTML = '';
      var query = (filterText || '').toLowerCase().trim();
      var countVisible = 0;

      for (var i = 0; i < selectEl.options.length; i++) {
        var opt = selectEl.options[i];
        var text = opt.textContent.trim();
        var val = opt.value;

        if (query && text.toLowerCase().indexOf(query) === -1) {
          continue;
        }

        countVisible++;
        var item = document.createElement('div');
        item.className = 'qbit-select-option' + (opt.selected ? ' is-selected' : '');
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
        item.dataset.value = val;
        item.dataset.index = i;

        var itemText = document.createElement('span');
        itemText.className = 'option-text';
        itemText.textContent = text;
        item.appendChild(itemText);

        var checkIcon = document.createElement('span');
        checkIcon.className = 'material-symbols-outlined check-icon';
        checkIcon.textContent = 'check';
        item.appendChild(checkIcon);

        (function (index, optionEl) {
          item.addEventListener('click', function (e) {
            e.stopPropagation();
            selectEl.selectedIndex = index;
            updateTriggerText();
            closeMenu();
            trigger.focus();

            // Dispatch native change event
            var event = new Event('change', { bubbles: true });
            selectEl.dispatchEvent(event);
            var inputEvent = new Event('input', { bubbles: true });
            selectEl.dispatchEvent(inputEvent);
          });
        })(i, opt);

        optionsList.appendChild(item);
      }

      if (countVisible === 0) {
        var empty = document.createElement('div');
        empty.className = 'qbit-select-empty';
        empty.textContent = 'No matching options';
        optionsList.appendChild(empty);
      }
    }

    function positionMenu() {
      var rect = trigger.getBoundingClientRect();
      var menuHeight = menu.offsetHeight || 220;
      var spaceBelow = window.innerHeight - rect.bottom;
      var spaceAbove = rect.top;

      menu.style.width = Math.max(rect.width, 200) + 'px';
      menu.style.left = rect.left + 'px';

      if (spaceBelow < menuHeight && spaceAbove > spaceBelow) {
        // Position above
        menu.style.top = Math.max(10, rect.top - menuHeight - 4) + 'px';
      } else {
        // Position below
        menu.style.top = (rect.bottom + 4) + 'px';
      }
    }

    var isOpen = false;

    function openMenu() {
      if (trigger.disabled) return;
      isOpen = true;
      trigger.setAttribute('aria-expanded', 'true');
      wrapper.classList.add('is-open');
      menu.style.display = 'flex';
      renderOptions('');
      if (searchInput) searchInput.value = '';
      positionMenu();

      if (searchInput) {
        setTimeout(function () { searchInput.focus(); }, 40);
      }

      // Scroll selected item into view
      var selectedItem = optionsList.querySelector('.is-selected');
      if (selectedItem) {
        selectedItem.scrollIntoView({ block: 'nearest' });
      }

      document.addEventListener('click', onDocClick);
      window.addEventListener('resize', positionMenu);
      window.addEventListener('scroll', onWindowScroll, true);
    }

    function closeMenu() {
      if (!isOpen) return;
      isOpen = false;
      trigger.setAttribute('aria-expanded', 'false');
      wrapper.classList.remove('is-open');
      menu.style.display = 'none';

      document.removeEventListener('click', onDocClick);
      window.removeEventListener('resize', positionMenu);
      window.removeEventListener('scroll', onWindowScroll, true);
    }

    function onDocClick(e) {
      if (!wrapper.contains(e.target) && !menu.contains(e.target)) {
        closeMenu();
      }
    }

    function onWindowScroll(e) {
      if (isOpen && !menu.contains(e.target)) {
        positionMenu();
      }
    }

    trigger.addEventListener('click', function (e) {
      e.preventDefault();
      if (isOpen) {
        closeMenu();
      } else {
        // Close any other open qbit-select menus
        document.querySelectorAll('.qbit-select-menu').forEach(function (m) {
          m.style.display = 'none';
        });
        document.querySelectorAll('.qbit-select-wrapper').forEach(function (w) {
          w.classList.remove('is-open');
        });
        openMenu();
      }
    });

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        renderOptions(searchInput.value);
      });
      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          closeMenu();
          trigger.focus();
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          var firstOpt = optionsList.querySelector('.qbit-select-option');
          if (firstOpt) firstOpt.focus();
        }
      });
    }

    // Keyboard support on trigger
    trigger.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        if (!isOpen) {
          openMenu();
        } else {
          closeMenu();
        }
      } else if (e.key === 'Escape') {
        closeMenu();
      }
    });

    // Listen to changes on native select (e.g. programmatically changed)
    selectEl.addEventListener('change', function () {
      updateTriggerText();
    });

    // Observe changes to options
    var observer = new MutationObserver(function () {
      updateTriggerText();
      if (isOpen) renderOptions(searchInput ? searchInput.value : '');
    });
    observer.observe(selectEl, { childList: true, subtree: true, attributes: true });

    // Initial trigger text
    updateTriggerText();
  }

  function initAllDropdowns() {
    var selects = document.querySelectorAll('select:not([data-qbit-enhanced="true"]):not([data-native="true"])');
    selects.forEach(function (sel) {
      initDropdown(sel);
    });
  }

  window.initQbitDropdowns = initAllDropdowns;

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllDropdowns);
  } else {
    initAllDropdowns();
  }

  // Also re-check when DOM changes (e.g. dynamic modals or wizard step transitions)
  var bodyObserver = new MutationObserver(function (mutations) {
    var hasNewSelects = false;
    for (var i = 0; i < mutations.length; i++) {
      if (mutations[i].addedNodes && mutations[i].addedNodes.length) {
        for (var j = 0; j < mutations[i].addedNodes.length; j++) {
          var node = mutations[i].addedNodes[j];
          if (node.nodeType === 1) {
            if (node.tagName === 'SELECT' || node.querySelector('select')) {
              hasNewSelects = true;
              break;
            }
          }
        }
      }
      if (hasNewSelects) break;
    }
    if (hasNewSelects) {
      initAllDropdowns();
    }
  });

  if (document.body) {
    bodyObserver.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      bodyObserver.observe(document.body, { childList: true, subtree: true });
    });
  }

})();
