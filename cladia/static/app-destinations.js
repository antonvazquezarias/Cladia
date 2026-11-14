// Result destinations shared by tool dialogs and combined library operations.
// A null folder means Library root; undefined means the user cancelled.
const RESULT_TOOLS = new Set(['align','assemble','map','tree','primers','download']);

function resultSourceItems(name, form) {
  if (name === 'map') return [];
  if (name === 'tree') return form._pickers?.alignment?.leafItems() || [];
  if (name === 'assemble') return form._pickers?.reads?.leafItems() || [];
  if (name === 'primers' && (form._operation?.id === 'primers.design' || form.mode?.value === 'design')) {
    const item = itemById(form._pickers?.template?.value || form.template?.value);
    return item ? [item] : (form._pickers?.sequences?.leafItems() || []).slice(0,1);
  }
  return form._pickers?.sequences?.leafItems() || [];
}

function resultFolderOptions(select, chosen, mixed = false) {
  select.replaceChildren();
  if (mixed) { const option = new Option('Choose a destination…', ''); option.disabled = true; select.append(option); }
  select.append(new Option('Library', '/'));
  for (const folder of folders) select.append(new Option(folder, folder));
  select.value = chosen === undefined ? '' : chosen || '/';
}

function renderResultDestination(form, name = form._toolName) {
  if (!RESULT_TOOLS.has(name)) return;
  let row = form.querySelector('.result-destination');
  if (!row) {
    row = document.createElement('label'); row.className = 'result-destination';
    row.append(document.createTextNode('Save result in'));
    const select = document.createElement('select'); select.required = true; select.setAttribute('aria-label','Save result in');
    select.onchange = () => { form._destinationChoice = select.value === '/' ? null : select.value; };
    row.append(select); form.querySelector('.error').before(row);
  }
  const sources = resultSourceItems(name, form), places = new Set(sources.map(folderOf));
  const mixed = places.size > 1;
  if (form._destinationChoice && !folders.includes(form._destinationChoice)) delete form._destinationChoice;
  const automatic = name === 'map' ? currentFolder : places.size === 1 ? [...places][0] : mixed ? undefined : currentFolder;
  resultFolderOptions(row.querySelector('select'), '_destinationChoice' in form ? form._destinationChoice : automatic, mixed);
}

async function chooseResultFolder(inputs) {
  const places = new Set(inputs.filter(Boolean).map(folderOf));
  if (places.size <= 1) return places.size ? [...places][0] : currentFolder;
  const dialog = document.createElement('dialog');
  dialog.innerHTML = `<form autocomplete="off" method="dialog"><h3>Result destination</h3>
    <p>The inputs are in different folders. Choose where to save the combined result.</p>
    <label>Save result in<select required aria-label="Save result in"></select></label>
    <footer><button value="cancel" formnovalidate class="secondary">Cancel</button><button value="save">Continue</button></footer></form>`;
  const select = dialog.querySelector('select');resultFolderOptions(select, undefined, true);
  document.body.append(dialog);
  return new Promise(resolve => {
    dialog.addEventListener('close',()=>{
      const folder = dialog.returnValue === 'save' ? (select.value === '/' ? null : select.value) : undefined;
      dialog.remove();resolve(folder);
    },{once:true});dialog.showModal();
  });
}
