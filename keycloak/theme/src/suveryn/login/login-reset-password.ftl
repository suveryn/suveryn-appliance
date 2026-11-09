<#-- keycloak.v2's login-reset-password.ftl (Keycloak 26.8) with one change: the instruction ("enter your
     username or e-mail address and we send you…") stands above the field, where it is read before the
     field is filled, instead of Keycloak's place under the buttons (the sign-in board A03). -->
<#import "template.ftl" as layout>
<#import "field.ftl" as field>
<#import "buttons.ftl" as buttons>
<@layout.registrationLayout displayInfo=false displayMessage=!messagesPerField.existsError('username'); section>
    <#if section = "header">
        ${msg("emailForgotTitle")}
    <#elseif section = "form">
        <form id="kc-reset-password-form" class="${properties.kcFormClass!}" action="${url.loginAction}" method="post">
            <p class="suv-instruction">
                <#if realm.duplicateEmailsAllowed>
                    ${msg("emailInstructionUsername")}
                <#else>
                    ${msg("emailInstruction")}
                </#if>
            </p>
            <#assign label>
                <#if !realm.loginWithEmailAllowed>${msg("username")}<#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}<#else>${msg("email")}</#if>
            </#assign>
            <@field.input name="username" label=label value=auth.attemptedUsername!'' autofocus=true />

            <@buttons.actionGroup>
              <@buttons.button id="kc-form-buttons" label="doSubmit"/>
              <@buttons.buttonLink href=url.loginUrl label="backToLogin"/>
            </@buttons.actionGroup>

        </form>
    </#if>
</@layout.registrationLayout>
