<#--
  Sūveryn email frame: the lockup, the message, the tagline. Light theme, inline styles, tables
  only, so it survives Outlook and webmail. Nothing is fetched (an air-gapped office must not, and
  clients block remote images): the icon is the brand's SVG inlined, and the wordmark is text in
  IBM Plex Mono where the reader has it, monospace otherwise. Clients that strip inline SVG
  (Gmail, Outlook for Windows) show the wordmark alone.
-->
<#macro emailLayout>
<!DOCTYPE html>
<html lang="${locale.language}" dir="${(ltr)?then('ltr','rtl')}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <style>
    .suv-body a { color: #BE2E00; text-decoration: underline; } /* brand accent-ink (tokens v3.4 sRGB): 4.5:1 */
  </style>
</head>
<body style="margin:0; padding:0; background:#FFFFFF; color:#010205;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FFFFFF;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
          <tr>
            <td style="padding:0 4px 20px;">
              <#-- The lockup: 30px icon, 12px gap, 22px wordmark (brand guidelines). -->
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="padding:0 12px 0 0; vertical-align:middle;"><#include "suveryn-icon.ftl"></td>
                  <td style="vertical-align:middle; font-family:'IBM Plex Mono', ui-monospace, Menlo, Consolas, monospace; font-weight:500; font-size:22px; line-height:1; color:#010205;">s&#363;veryn</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="suv-body" style="background:#F8F8FA; border:1px solid rgba(1,2,5,0.12); border-radius:14px; padding:24px 28px; font-family:Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size:15px; line-height:1.6; color:#010205;">
              <#nested>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 4px 0; font-family:Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size:12px; line-height:1.5; color:rgba(1,2,5,0.62);">
              ${msg("suverynTagline")}<br>
              ${msg("suverynEmailFooter")}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
</#macro>
