--!strict
-- Einstieg auf dem Client. Rojo legt diesen Ordner als LocalScript "Client" in StarterPlayerScripts an.
-- Hier laufen Steuerung, Oberfläche und Effekte für den eigenen Spieler.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Konfig = require(ReplicatedStorage.Shared.Konfig)

print(`[{Konfig.SpielName}] Hallo {Players.LocalPlayer.Name}!`)
