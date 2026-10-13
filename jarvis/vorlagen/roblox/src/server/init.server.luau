--!strict
-- Einstieg auf dem Server. Rojo legt diesen Ordner als Script "Server" in ServerScriptService an.
-- Hier läuft alles, was vertrauenswürdig sein muss: Spiellogik, Belohnungen, gespeicherte Daten.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Konfig = require(ReplicatedStorage.Shared.Konfig)

print(`[{Konfig.SpielName}] Server gestartet`)
