-- Personal setup helper for Vanilla 1.12; no saved account or inventory rules.
local function say(message)
  DEFAULT_CHAT_FRAME:AddMessage("|cff80ff80FishingBotSetup|r: " .. message)
end
local bodies = {
  ["FB-Lure"] = '/script for b=0,4 do for s=1,GetContainerNumSlots(b) do local l=strlower(GetContainerItemLink(b,s) or "");if strfind(l,"aquadynamic fish attractor",1,true) then UseContainerItem(b,s);if SpellIsTargeting() then PickupInventoryItem(16);ReplaceEnchant();end return end end end',
  ["FB-Boxes"] = '/script for b=0,4 do for s=1,GetContainerNumSlots(b) do local l=strlower(GetContainerItemLink(b,s) or "");if strfind(l,"bloated mightfish",1,true) or strfind(l,"big-mouth clam",1,true) or strfind(l,"mithril bound trunk",1,true) or strfind(l,"heavy crate",1,true) then UseContainerItem(b,s);return end end end'
}

local function findMacro(name)
  for index = 1, (MAX_MACROS or 36) do
    local current = GetMacroInfo(index)
    if current == name then return index end
  end
  return nil
end

local function createMacros()
  for _, name in ipairs({"FB-Lure", "FB-Boxes"}) do
    local index = findMacro(name)
    if index and index > 0 then
      local existingName, icon, body = GetMacroInfo(index)
      if body ~= bodies[name] then
        say(name .. " уже существует с другим текстом. Он не изменён; проверьте его вручную.")
      else
        say(name .. " уже готов.")
      end
    else
      local icon = GetMacroIconInfo(1)
      local ok, result = pcall(CreateMacro, name, icon, bodies[name], 1)
      if ok and findMacro(name) then
        say("Создан макрос " .. name .. ".")
      else
        say("Не удалось создать " .. name .. ": проверьте свободные места для макросов и загрузку SuperWoW.")
      end
    end
  end
end

local function autoloot(value)
  if not SuperAPI or not SuperAPI.cmdtable or not SuperAPI.cmdtable.args.autoloot then
    say("SuperAPI не загружен. Проверьте SuperWoW >= 2.2 и список аддонов.")
    return
  end
  local mode = value and "Always on" or "Always off"
  SuperAPI.cmdtable.args.autoloot.set(mode)
  say("Автолут: " .. mode .. ". Проверьте сбор без Shift. Если работает наоборот: /fishsetup autoloot " .. (value and "off" or "on"))
end

SLASH_FISHINGBOTSETUP1 = "/fishsetup"
SlashCmdList["FISHINGBOTSETUP"] = function(message)
  local command = string.lower(message or "")
  if command == "help" then
    say("/fishsetup — создать макросы и включить автолут; /fishsetup macros; /fishsetup autoloot on|off.")
    return
  end
  if UnitAffectingCombat("player") then say("Выйдите из боя перед настройкой."); return end
  if not tonumber(SUPERWOW_VERSION) or tonumber(SUPERWOW_VERSION) < 2.2 then
    say("Нужен SuperWoW >= 2.2. Запускайте игру через ярлык Start WoW for Fishing.")
    return
  end
  if command == "" or command == "macros" then
    createMacros()
    say("Положите Fishing на 1, FB-Lure на 2 и FB-Boxes на 3. Существующие кнопки и привязки не менялись.")
  end
  if command == "" or command == "autoloot on" then autoloot(true)
  elseif command == "autoloot off" then autoloot(false)
  elseif command ~= "macros" then say("Неизвестная команда. /fishsetup help") end
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")
frame:SetScript("OnEvent", function()
  say("Для подготовки рыбалки выполните /fishsetup. Автоудаление настраивается отдельно через /sad.")
end)
