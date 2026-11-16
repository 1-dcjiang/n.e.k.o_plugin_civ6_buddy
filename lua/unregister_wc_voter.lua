
if __civ6_wc_handler then
  local ok, err = pcall(function() Events.WorldCongressStage1.Remove(__civ6_wc_handler) end)
  if not ok then print("WC_UNREGISTER_ERR|" .. tostring(err)) return end
  __civ6_wc_handler = nil
  __civ6_wc_voted = nil
  print("WC_UNREGISTERED|ok")
else
  print("WC_UNREGISTERED|absent")
end
