// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// DOM Helpers
// Reusable DOM utilities for renderer components

class DomHelpers {
  // Element selection and manipulation
  static getElementById(id) {
    return document.getElementById(id);
  }

  static querySelector(selector) {
    return document.querySelector(selector);
  }

  static querySelectorAll(selector) {
    return document.querySelectorAll(selector);
  }

  static createElement(tagName, className = '', innerHTML = '') {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (innerHTML) element.innerHTML = innerHTML;
    return element;
  }

  // Class management
  static addClass(element, className) {
    if (element) element.classList.add(className);
  }

  static removeClass(element, className) {
    if (element) element.classList.remove(className);
  }

  static toggleClass(element, className) {
    if (element) element.classList.toggle(className);
  }

  static hasClass(element, className) {
    return element ? element.classList.contains(className) : false;
  }

  // Content management
  static setHTML(element, html) {
    if (element) element.innerHTML = html;
  }

  static setText(element, text) {
    if (element) element.textContent = text;
  }

  static appendHTML(element, html) {
    if (element) element.innerHTML += html;
  }

  static clearContent(element) {
    if (element) element.innerHTML = '';
  }

  // Event handling
  static addEventListener(element, event, handler) {
    if (element) element.addEventListener(event, handler);
  }

  static removeEventListener(element, event, handler) {
    if (element) element.removeEventListener(event, handler);
  }

  // Form utilities
  static getFormData(formElement) {
    if (!formElement) return {};
    
    const formData = new FormData(formElement);
    const data = {};
    
    for (const [key, value] of formData.entries()) {
      data[key] = value;
    }
    
    return data;
  }

  static setFormData(formElement, data) {
    if (!formElement || !data) return;
    
    Object.keys(data).forEach(key => {
      const input = formElement.querySelector(`[name="${key}"]`);
      if (input) {
        input.value = data[key] || '';
      }
    });
  }

  static clearForm(formElement) {
    if (formElement) formElement.reset();
  }

  // Visibility management
  static show(element) {
    if (element) element.style.display = '';
  }

  static hide(element) {
    if (element) element.style.display = 'none';
  }

  static toggle(element) {
    if (element) {
      element.style.display = element.style.display === 'none' ? '' : 'none';
    }
  }

  // Animation helpers
  static fadeIn(element, duration = 300) {
    if (!element) return;
    
    element.style.opacity = '0';
    element.style.display = '';
    
    let start = null;
    const animate = (timestamp) => {
      if (!start) start = timestamp;
      const progress = timestamp - start;
      
      element.style.opacity = Math.min(progress / duration, 1);
      
      if (progress < duration) {
        requestAnimationFrame(animate);
      }
    };
    
    requestAnimationFrame(animate);
  }

  static fadeOut(element, duration = 300) {
    if (!element) return;
    
    let start = null;
    const animate = (timestamp) => {
      if (!start) start = timestamp;
      const progress = timestamp - start;
      
      element.style.opacity = 1 - Math.min(progress / duration, 1);
      
      if (progress < duration) {
        requestAnimationFrame(animate);
      } else {
        element.style.display = 'none';
      }
    };
    
    requestAnimationFrame(animate);
  }

  // Scroll utilities
  static scrollToBottom(element) {
    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  }

  static scrollToTop(element) {
    if (element) {
      element.scrollTop = 0;
    }
  }

  // HTML escaping
  static escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  static unescapeHtml(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div.textContent || div.innerText || '';
  }

  // Loading state management
  static showLoading(element, text = 'Loading...') {
    if (!element) return;
    
    element.innerHTML = `
      <div class="loading-state">
        <div class="loading-spinner"></div>
        <div class="loading-text">${text}</div>
      </div>
    `;
  }

  static hideLoading(element, originalContent = '') {
    if (element) {
      element.innerHTML = originalContent;
    }
  }
}

// Export for use in components
window.DomHelpers = DomHelpers;
export default DomHelpers;