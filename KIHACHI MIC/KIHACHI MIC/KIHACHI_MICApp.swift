//
//  KIHACHI_MICApp.swift
//  KIHACHI MIC
//
//  Created by user on 2026/10/22.
//

import SwiftUI
import CoreData

@main
struct KIHACHI_MICApp: App {
    let persistenceController = PersistenceController.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(\.managedObjectContext, persistenceController.container.viewContext)
        }
    }
}
