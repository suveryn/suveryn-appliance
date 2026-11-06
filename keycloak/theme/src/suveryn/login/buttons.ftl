<#-- The sūveryn form buttons (suveryn-tracker#99): keycloak.v2's buttons.ftl, with one change in how a group
     of buttons is laid out, so the sign-in pages follow the chat UI's dialogs (suveryn-core
     docs/design/chat-ui.md, "Dialogs"). Every keycloak.v2 page builds its buttons with these macros, so
     this one template covers them all.

     - One button (Sign in, Submit): full width, as before.
     - Two or more: one right-aligned row (suv-actions-row in suveryn.css), written in reading order,
       the way out (Cancel, No, Back to login) first and the main action (primary, orange) last, so Tab
       and a screen reader follow what the eye sees. The pages write the main action first; the group
       collects the buttons and writes them in that order. On a phone, a column in the same order.
     - A group with markup of its own (WebAuthn's pages put a form inside it) is left exactly as the
       page wrote it, so no button moves out of its form.
     - Deleting (doConfirmDelete): the focus starts on the way out. -->

<#macro actionGroup horizontal=false>
  <#global suvInGroup = true>
  <#global suvPrimary></#global>
  <#global suvOthers></#global>
  <#global suvCount = 0>
  <#global suvRisky = false>
  <#local content><#nested></#local>
  <#global suvInGroup = false>
  <#local keep = content?markup_string?contains("<form")>
  <div class="${properties.kcFormGroupClass}">
    <div class="${properties.kcFormActionGroupClass} <#if horizontal>pf-v5-u-flex-nowrap<#else>pf-v5-u-flex-wrap</#if><#if suvCount gt 1 && !keep> suv-actions-row</#if>">
      <#if keep || suvCount lt 2>${content}<#else>${suvOthers}${suvPrimary}</#if>
    </div>
  </div>
</#macro>

<#macro button label id="" name="" type="primary" fullWidth=true class=[] extra...>
  <#if type == "primary" && label == "doConfirmDelete"><#global suvRisky = true></#if>
  <#local html><button class="${properties['kcButton' + type?cap_first + 'Class']}<#if fullWidth> ${properties.kcButtonBlockClass}</#if><#list class as c> ${properties[c]}</#list>" name="${name}" id="${id}"
          type="submit"<#if type != "primary" && (suvRisky!false)> autofocus</#if> <#list extra as attrName, attrVal>${attrName}="${attrVal}"</#list>>
  ${msg(label)}
  </button></#local>
  ${html}
  <#if suvInGroup!false>
    <#global suvCount = suvCount + 1>
    <#if type == "primary"><#global suvPrimary>${suvPrimary}${html}</#global><#else><#global suvOthers>${suvOthers}${html}</#global></#if>
  </#if>
</#macro>

<#macro buttonLink href label id="" class=["kcButtonSecondaryClass", "kcButtonBlockClass"]>
  <#local html><a id="${id}" href="${href}" class="<#list class as c>${properties[c]} </#list>">${msg(label)}</a></#local>
  ${html}
  <#if suvInGroup!false>
    <#global suvCount = suvCount + 1>
    <#global suvOthers>${suvOthers}${html}</#global>
  </#if>
</#macro>

<#macro loginButton>
  <@actionGroup>
    <@button id="kc-login" name="login" label="doLogIn" />
  </@actionGroup>
</#macro>
